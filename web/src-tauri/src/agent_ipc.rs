use std::collections::{BTreeMap, HashMap};
use std::io::{BufRead, BufReader, Write};
use std::path::PathBuf;
use std::process::{Child, ChildStdin, Command, Stdio};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::Duration;

use serde::Deserialize;
use serde_json::{json, Value};
use tauri::ipc::Channel;
use tauri::AppHandle;
#[cfg(not(debug_assertions))]
use tauri::Manager;

const MAX_BODY_BYTES: usize = 2_000_000;
const MAX_PATH_BYTES: usize = 8_192;
const MAX_HEADER_COUNT: usize = 64;
const MAX_HEADER_BYTES: usize = 8_192;

#[derive(Deserialize)]
pub struct AgentRequest {
    method: String,
    path: String,
    #[serde(default)]
    headers: BTreeMap<String, String>,
    body: Option<String>,
}

impl AgentRequest {
    fn validate(&self) -> Result<(), String> {
        if !matches!(self.method.as_str(), "GET" | "POST" | "PUT") {
            return Err("The Agent IPC method must be GET, POST, or PUT".into());
        }
        if !self.path.starts_with('/')
            || self.path.starts_with("//")
            || self.path.len() > MAX_PATH_BYTES
            || self.path.chars().any(char::is_control)
        {
            return Err("The Agent IPC path is invalid".into());
        }
        if self.headers.len() > MAX_HEADER_COUNT
            || self.headers.iter().any(|(name, value)| {
                name.is_empty()
                    || name.len() + value.len() > MAX_HEADER_BYTES
                    || name.chars().any(char::is_control)
                    || value.contains(['\r', '\n'])
            })
        {
            return Err("The Agent IPC headers are invalid".into());
        }
        if self
            .body
            .as_ref()
            .is_some_and(|body| body.len() > MAX_BODY_BYTES)
        {
            return Err("The Agent IPC request body is too large".into());
        }
        Ok(())
    }
}

struct ManagedChild {
    child: Child,
    stdin: ChildStdin,
}

pub struct AgentIpc {
    process: Mutex<Option<ManagedChild>>,
    pending: Arc<Mutex<HashMap<String, Channel<Value>>>>,
    next_id: AtomicU64,
    generation: Arc<AtomicU64>,
}

impl AgentIpc {
    pub fn new() -> Self {
        Self {
            process: Mutex::new(None),
            pending: Arc::new(Mutex::new(HashMap::new())),
            next_id: AtomicU64::new(1),
            generation: Arc::new(AtomicU64::new(0)),
        }
    }

    pub fn start(
        &self,
        app: &AppHandle,
        environment: &BTreeMap<String, String>,
    ) -> Result<(), String> {
        let (mut command, working_directory) = backend_command(app)?;
        command
            .current_dir(working_directory)
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::null())
            .env("PYTHONUNBUFFERED", "1")
            .env("PYTHONDONTWRITEBYTECODE", "1");
        for (name, value) in environment {
            command.env(name, value);
        }

        #[cfg(target_os = "windows")]
        {
            use std::os::windows::process::CommandExt;
            command.creation_flags(0x08000000);
        }

        let mut child = command
            .spawn()
            .map_err(|error| format!("Could not start the local Agent engine: {error}"))?;
        let stdin = child
            .stdin
            .take()
            .ok_or_else(|| "The local Agent engine did not expose stdin".to_owned())?;
        let stdout = child
            .stdout
            .take()
            .ok_or_else(|| "The local Agent engine did not expose stdout".to_owned())?;

        let generation = self.generation.fetch_add(1, Ordering::SeqCst) + 1;
        let pending = self.pending.clone();
        let current_generation = self.generation.clone();
        thread::Builder::new()
            .name("agent-ipc-reader".into())
            .spawn(move || {
                let reader = BufReader::new(stdout);
                for line in reader.lines() {
                    let Ok(line) = line else { break };
                    let Ok(message) = serde_json::from_str::<Value>(&line) else {
                        continue;
                    };
                    let Some(request_id) =
                        message.get("id").and_then(Value::as_str).map(str::to_owned)
                    else {
                        continue;
                    };
                    let terminal = matches!(
                        message.get("type").and_then(Value::as_str),
                        Some("response_end" | "error" | "cancelled")
                    );
                    let channel = pending
                        .lock()
                        .ok()
                        .and_then(|requests| requests.get(&request_id).cloned());
                    if let Some(channel) = channel {
                        if channel.send(message).is_err() || terminal {
                            if let Ok(mut requests) = pending.lock() {
                                requests.remove(&request_id);
                            }
                        }
                    }
                }

                if current_generation.load(Ordering::SeqCst) == generation {
                    fail_pending_requests(&pending, "The local Agent engine stopped unexpectedly");
                }
            })
            .map_err(|error| format!("Could not start the Agent IPC reader: {error}"))?;

        let mut process = self
            .process
            .lock()
            .map_err(|_| "Could not lock the local Agent engine".to_owned())?;
        *process = Some(ManagedChild { child, stdin });
        Ok(())
    }

    pub fn restart(
        &self,
        app: &AppHandle,
        environment: &BTreeMap<String, String>,
    ) -> Result<(), String> {
        self.stop();
        self.start(app, environment)
    }

    pub fn stop(&self) {
        self.generation.fetch_add(1, Ordering::SeqCst);
        fail_pending_requests(&self.pending, "The local Agent engine is restarting");
        if let Ok(mut process) = self.process.lock() {
            if let Some(mut managed) = process.take() {
                drop(managed.stdin);
                for _ in 0..100 {
                    match managed.child.try_wait() {
                        Ok(Some(_)) => return,
                        Ok(None) => thread::sleep(Duration::from_millis(25)),
                        Err(_) => break,
                    }
                }
                let _ = managed.child.kill();
                let _ = managed.child.wait();
            }
        }
    }

    pub fn request(
        &self,
        request: AgentRequest,
        on_event: Channel<Value>,
    ) -> Result<String, String> {
        request.validate()?;
        let request_id = format!("desktop-{}", self.next_id.fetch_add(1, Ordering::Relaxed));
        let message = json!({
            "type": "request",
            "id": request_id,
            "method": request.method,
            "path": request.path,
            "headers": request.headers,
            "body": request.body,
        });
        let serialized = serde_json::to_string(&message)
            .map_err(|error| format!("Could not encode the Agent IPC request: {error}"))?;

        self.pending
            .lock()
            .map_err(|_| "Could not lock Agent IPC requests".to_owned())?
            .insert(request_id.clone(), on_event);

        let write_result = self
            .process
            .lock()
            .map_err(|_| "Could not lock the local Agent engine".to_owned())?
            .as_mut()
            .ok_or_else(|| "The local Agent engine is not running".to_owned())
            .and_then(|managed| {
                managed
                    .stdin
                    .write_all(serialized.as_bytes())
                    .and_then(|_| managed.stdin.write_all(b"\n"))
                    .and_then(|_| managed.stdin.flush())
                    .map_err(|error| format!("Could not send the Agent IPC request: {error}"))
            });
        if let Err(error) = write_result {
            if let Ok(mut requests) = self.pending.lock() {
                requests.remove(&request_id);
            }
            return Err(error);
        }
        Ok(request_id)
    }

    pub fn cancel(&self, request_id: &str) -> Result<(), String> {
        if request_id.is_empty()
            || request_id.len() > 100
            || request_id.chars().any(char::is_control)
        {
            return Err("The Agent IPC request id is invalid".into());
        }
        if let Ok(mut requests) = self.pending.lock() {
            requests.remove(request_id);
        }
        let serialized = serde_json::to_string(&json!({
            "type": "cancel",
            "id": request_id,
        }))
        .map_err(|error| format!("Could not encode the Agent IPC cancellation: {error}"))?;
        let mut process = self
            .process
            .lock()
            .map_err(|_| "Could not lock the local Agent engine".to_owned())?;
        let managed = process
            .as_mut()
            .ok_or_else(|| "The local Agent engine is not running".to_owned())?;
        managed
            .stdin
            .write_all(serialized.as_bytes())
            .and_then(|_| managed.stdin.write_all(b"\n"))
            .and_then(|_| managed.stdin.flush())
            .map_err(|error| format!("Could not cancel the Agent IPC request: {error}"))
    }
}

fn fail_pending_requests(pending: &Arc<Mutex<HashMap<String, Channel<Value>>>>, message: &str) {
    let requests = pending
        .lock()
        .map(|mut requests| requests.drain().collect::<Vec<_>>())
        .unwrap_or_default();
    for (request_id, channel) in requests {
        let _ = channel.send(json!({
            "id": request_id,
            "type": "error",
            "message": message,
        }));
    }
}

#[cfg(debug_assertions)]
fn backend_command(_app: &AppHandle) -> Result<(Command, PathBuf), String> {
    let tauri_dir = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    let repository_root = tauri_dir
        .parent()
        .and_then(|web| web.parent())
        .ok_or_else(|| "Could not resolve the repository root".to_owned())?
        .to_path_buf();
    let python = if cfg!(windows) {
        repository_root
            .join(".venv")
            .join("Scripts")
            .join("python.exe")
    } else {
        repository_root.join(".venv").join("bin").join("python")
    };
    if !python.exists() {
        return Err(format!(
            "The development Python environment was not found at {}",
            python.display()
        ));
    }
    let mut command = Command::new(python);
    command.args(["-m", "agent_product.ipc"]);
    Ok((command, repository_root))
}

#[cfg(not(debug_assertions))]
fn backend_command(app: &AppHandle) -> Result<(Command, PathBuf), String> {
    let executable_name = if cfg!(windows) {
        "agent-product-sidecar.exe"
    } else {
        "agent-product-sidecar"
    };
    let resource_dir = app.path().resource_dir().map_err(|error| {
        format!("Could not resolve the application resource directory: {error}")
    })?;
    let current_executable_dir = std::env::current_exe()
        .ok()
        .and_then(|path| path.parent().map(PathBuf::from));
    let candidates = [
        resource_dir.join(executable_name),
        resource_dir.join("binaries").join(executable_name),
        current_executable_dir
            .unwrap_or_default()
            .join(executable_name),
    ];
    let executable = candidates
        .into_iter()
        .find(|candidate| candidate.is_file())
        .ok_or_else(|| "The bundled Agent sidecar executable is missing".to_owned())?;
    let working_directory = app
        .path()
        .app_data_dir()
        .map_err(|error| format!("Could not resolve the application data directory: {error}"))?;
    std::fs::create_dir_all(working_directory.join("data"))
        .and_then(|_| std::fs::create_dir_all(working_directory.join("knowledge")))
        .map_err(|error| format!("Could not prepare the application data directory: {error}"))?;
    Ok((Command::new(executable), working_directory))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn rejects_remote_or_oversized_requests() {
        let remote = AgentRequest {
            method: "GET".into(),
            path: "https://example.com".into(),
            headers: BTreeMap::new(),
            body: None,
        };
        assert!(remote.validate().is_err());

        let oversized = AgentRequest {
            method: "POST".into(),
            path: "/v1/chat".into(),
            headers: BTreeMap::new(),
            body: Some("x".repeat(MAX_BODY_BYTES + 1)),
        };
        assert!(oversized.validate().is_err());
    }
}
