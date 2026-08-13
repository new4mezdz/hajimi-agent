#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::collections::BTreeMap;
use std::fs;
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;

use serde::{Deserialize, Serialize};
use tauri::{AppHandle, Manager, RunEvent, State};

const SETTINGS_FILE: &str = "agent-settings.json";

struct BackendProcess(Mutex<Option<Child>>);

#[derive(Clone, Deserialize, Serialize)]
#[serde(default, rename_all = "camelCase")]
struct StoredAgentSettings {
    version: u8,
    provider: String,
    model: String,
    web_search_enabled: bool,
    workspace_write_enabled: bool,
    agent_instructions: String,
    encrypted_api_keys: BTreeMap<String, String>,
}

impl Default for StoredAgentSettings {
    fn default() -> Self {
        Self {
            version: 1,
            provider: "openai".into(),
            model: "openai:gpt-4.1-mini".into(),
            web_search_enabled: true,
            workspace_write_enabled: true,
            agent_instructions:
                ("You are 哈基米sama, a reliable local coding agent. Be concise, accurate, "
                    .to_owned()
                    + "inspect the workspace before making code claims, and use tools when useful."),
            encrypted_api_keys: BTreeMap::new(),
        }
    }
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct PublicAgentSettings {
    provider: String,
    model: String,
    web_search_enabled: bool,
    workspace_write_enabled: bool,
    agent_instructions: String,
    api_key_configured: bool,
    configured_providers: Vec<String>,
    secure_storage: bool,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
struct AgentSettingsInput {
    provider: String,
    model: String,
    web_search_enabled: bool,
    workspace_write_enabled: bool,
    agent_instructions: String,
    api_key: Option<String>,
    clear_api_key: bool,
}

fn settings_path(app: &AppHandle) -> Result<PathBuf, String> {
    app.path()
        .app_config_dir()
        .map(|directory| directory.join(SETTINGS_FILE))
        .map_err(|error| format!("Could not resolve the app config directory: {error}"))
}

fn load_settings(app: &AppHandle) -> Result<StoredAgentSettings, String> {
    let path = settings_path(app)?;
    if !path.exists() {
        return Ok(StoredAgentSettings::default());
    }
    let content = fs::read_to_string(&path)
        .map_err(|error| format!("Could not read {}: {error}", path.display()))?;
    serde_json::from_str(&content)
        .map_err(|error| format!("Could not parse {}: {error}", path.display()))
}

fn save_settings_file(app: &AppHandle, settings: &StoredAgentSettings) -> Result<(), String> {
    let path = settings_path(app)?;
    let parent = path
        .parent()
        .ok_or_else(|| "The settings path has no parent directory".to_owned())?;
    fs::create_dir_all(parent)
        .map_err(|error| format!("Could not create {}: {error}", parent.display()))?;
    let temporary = path.with_extension("json.tmp");
    let content = serde_json::to_vec_pretty(settings)
        .map_err(|error| format!("Could not serialize settings: {error}"))?;
    fs::write(&temporary, content)
        .map_err(|error| format!("Could not write {}: {error}", temporary.display()))?;
    if path.exists() {
        fs::remove_file(&path)
            .map_err(|error| format!("Could not replace {}: {error}", path.display()))?;
    }
    fs::rename(&temporary, &path)
        .map_err(|error| format!("Could not replace {}: {error}", path.display()))
}

fn bytes_to_hex(bytes: &[u8]) -> String {
    const HEX: &[u8; 16] = b"0123456789abcdef";
    let mut output = String::with_capacity(bytes.len() * 2);
    for byte in bytes {
        output.push(HEX[(byte >> 4) as usize] as char);
        output.push(HEX[(byte & 0x0f) as usize] as char);
    }
    output
}

fn hex_to_bytes(value: &str) -> Result<Vec<u8>, String> {
    if value.len() % 2 != 0 {
        return Err("The encrypted API key has an invalid length".into());
    }
    (0..value.len())
        .step_by(2)
        .map(|index| {
            u8::from_str_radix(&value[index..index + 2], 16)
                .map_err(|_| "The encrypted API key is not valid hex".to_owned())
        })
        .collect()
}

#[cfg(windows)]
fn protect_secret(secret: &str) -> Result<String, String> {
    use std::ptr;
    use windows_sys::Win32::Foundation::LocalFree;
    use windows_sys::Win32::Security::Cryptography::{
        CryptProtectData, CRYPTPROTECT_UI_FORBIDDEN, CRYPT_INTEGER_BLOB,
    };

    let bytes = secret.as_bytes();
    let input = CRYPT_INTEGER_BLOB {
        cbData: bytes
            .len()
            .try_into()
            .map_err(|_| "The API key is too large".to_owned())?,
        pbData: bytes.as_ptr().cast_mut(),
    };
    let mut output = CRYPT_INTEGER_BLOB {
        cbData: 0,
        pbData: ptr::null_mut(),
    };
    let succeeded = unsafe {
        CryptProtectData(
            &input,
            ptr::null(),
            ptr::null(),
            ptr::null_mut(),
            ptr::null_mut(),
            CRYPTPROTECT_UI_FORBIDDEN,
            &mut output,
        )
    };
    if succeeded == 0 {
        return Err(format!(
            "Windows DPAPI could not encrypt the API key: {}",
            std::io::Error::last_os_error()
        ));
    }
    let encrypted = unsafe { std::slice::from_raw_parts(output.pbData, output.cbData as usize) };
    let encoded = bytes_to_hex(encrypted);
    unsafe {
        LocalFree(output.pbData.cast());
    }
    Ok(encoded)
}

#[cfg(windows)]
fn unprotect_secret(encrypted: &str) -> Result<String, String> {
    use std::ptr;
    use windows_sys::Win32::Foundation::LocalFree;
    use windows_sys::Win32::Security::Cryptography::{
        CryptUnprotectData, CRYPTPROTECT_UI_FORBIDDEN, CRYPT_INTEGER_BLOB,
    };

    let bytes = hex_to_bytes(encrypted)?;
    let input = CRYPT_INTEGER_BLOB {
        cbData: bytes
            .len()
            .try_into()
            .map_err(|_| "The encrypted API key is too large".to_owned())?,
        pbData: bytes.as_ptr().cast_mut(),
    };
    let mut output = CRYPT_INTEGER_BLOB {
        cbData: 0,
        pbData: ptr::null_mut(),
    };
    let succeeded = unsafe {
        CryptUnprotectData(
            &input,
            ptr::null_mut(),
            ptr::null(),
            ptr::null_mut(),
            ptr::null_mut(),
            CRYPTPROTECT_UI_FORBIDDEN,
            &mut output,
        )
    };
    if succeeded == 0 {
        return Err(format!(
            "Windows DPAPI could not decrypt the API key: {}",
            std::io::Error::last_os_error()
        ));
    }
    let decrypted = unsafe { std::slice::from_raw_parts(output.pbData, output.cbData as usize) };
    let result = String::from_utf8(decrypted.to_vec())
        .map_err(|_| "The decrypted API key is not UTF-8".to_owned());
    unsafe {
        LocalFree(output.pbData.cast());
    }
    result
}

#[cfg(not(windows))]
fn protect_secret(_secret: &str) -> Result<String, String> {
    Err("Secure API-key storage is currently implemented for Windows only".into())
}

#[cfg(not(windows))]
fn unprotect_secret(_encrypted: &str) -> Result<String, String> {
    Err("Secure API-key storage is currently implemented for Windows only".into())
}

fn public_settings(settings: &StoredAgentSettings) -> PublicAgentSettings {
    PublicAgentSettings {
        provider: settings.provider.clone(),
        model: settings.model.clone(),
        web_search_enabled: settings.web_search_enabled,
        workspace_write_enabled: settings.workspace_write_enabled,
        agent_instructions: settings.agent_instructions.clone(),
        api_key_configured: settings.encrypted_api_keys.contains_key(&settings.provider),
        configured_providers: settings.encrypted_api_keys.keys().cloned().collect(),
        secure_storage: cfg!(windows),
    }
}

fn validate_settings(input: &AgentSettingsInput) -> Result<(), String> {
    if !matches!(input.provider.as_str(), "openai" | "deepseek" | "anthropic") {
        return Err("Unsupported model provider".into());
    }
    let expected_prefix = format!("{}:", input.provider);
    if input.model.len() > 200
        || !input.model.starts_with(&expected_prefix)
        || input.model.chars().any(char::is_control)
    {
        return Err(format!("The model ID must start with {expected_prefix}"));
    }
    if input.agent_instructions.trim().is_empty() || input.agent_instructions.len() > 20_000 {
        return Err("Agent instructions must contain between 1 and 20,000 characters".into());
    }
    if input.api_key.as_ref().is_some_and(|key| key.len() > 8192) {
        return Err("The API key is too large".into());
    }
    Ok(())
}

fn selected_api_key(settings: &StoredAgentSettings) -> Result<Option<String>, String> {
    settings
        .encrypted_api_keys
        .get(&settings.provider)
        .map(|encrypted| unprotect_secret(encrypted))
        .transpose()
}

#[cfg(debug_assertions)]
fn local_backend_command() -> Option<(PathBuf, PathBuf)> {
    let tauri_dir = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    let repository_root = tauri_dir.parent()?.parent()?.to_path_buf();
    let python = repository_root
        .join(".venv")
        .join("Scripts")
        .join("python.exe");
    python.exists().then_some((python, repository_root))
}

fn configure_backend_environment(command: &mut Command, settings: &StoredAgentSettings) {
    command
        .env("AI_MODEL", &settings.model)
        .env("AGENT_INSTRUCTIONS", &settings.agent_instructions)
        .env(
            "WEB_SEARCH_ENABLED",
            settings.web_search_enabled.to_string(),
        )
        .env(
            "WORKSPACE_WRITE_ENABLED",
            settings.workspace_write_enabled.to_string(),
        );

    let variable = match settings.provider.as_str() {
        "deepseek" => "DEEPSEEK_API_KEY",
        "anthropic" => "ANTHROPIC_API_KEY",
        _ => "OPENAI_API_KEY",
    };
    if let Ok(Some(key)) = selected_api_key(settings) {
        command.env(variable, key);
    }
}

#[cfg(debug_assertions)]
fn spawn_local_backend(settings: &StoredAgentSettings) -> Option<Child> {
    let (python, repository_root) = local_backend_command()?;
    let mut command = Command::new(python);
    command
        .args(["-m", "agent_product"])
        .current_dir(repository_root)
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null());
    configure_backend_environment(&mut command, settings);

    #[cfg(target_os = "windows")]
    {
        use std::os::windows::process::CommandExt;
        command.creation_flags(0x08000000);
    }

    command.spawn().ok()
}

#[cfg(not(debug_assertions))]
fn spawn_local_backend(_settings: &StoredAgentSettings) -> Option<Child> {
    None
}

fn stop_backend(backend: &BackendProcess) {
    if let Ok(mut child) = backend.0.lock() {
        if let Some(mut process) = child.take() {
            let _ = process.kill();
            let _ = process.wait();
        }
    }
}

fn restart_backend(backend: &BackendProcess, settings: &StoredAgentSettings) -> Result<(), String> {
    stop_backend(backend);
    let process = spawn_local_backend(settings)
        .ok_or_else(|| "Could not restart the local Agent service".to_owned())?;
    let mut child = backend
        .0
        .lock()
        .map_err(|_| "Could not lock the Agent service process".to_owned())?;
    *child = Some(process);
    Ok(())
}

#[tauri::command]
fn get_agent_settings(app: AppHandle) -> Result<PublicAgentSettings, String> {
    load_settings(&app).map(|settings| public_settings(&settings))
}

#[tauri::command]
fn save_agent_settings(
    app: AppHandle,
    backend: State<'_, BackendProcess>,
    input: AgentSettingsInput,
) -> Result<PublicAgentSettings, String> {
    validate_settings(&input)?;
    let mut settings = load_settings(&app)?;
    settings.provider = input.provider;
    settings.model = input.model;
    settings.web_search_enabled = input.web_search_enabled;
    settings.workspace_write_enabled = input.workspace_write_enabled;
    settings.agent_instructions = input.agent_instructions.trim().to_owned();

    if input.clear_api_key {
        settings.encrypted_api_keys.remove(&settings.provider);
    } else if let Some(api_key) = input.api_key.filter(|key| !key.trim().is_empty()) {
        settings
            .encrypted_api_keys
            .insert(settings.provider.clone(), protect_secret(api_key.trim())?);
    }

    save_settings_file(&app, &settings)?;
    restart_backend(&backend, &settings)?;
    Ok(public_settings(&settings))
}

fn remove_stale_temporary_settings(path: &Path) {
    let temporary = path.with_extension("json.tmp");
    let _ = fs::remove_file(temporary);
}

fn main() {
    let application = tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .invoke_handler(tauri::generate_handler![
            get_agent_settings,
            save_agent_settings
        ])
        .setup(|app| {
            let handle = app.handle().clone();
            let path = settings_path(&handle).map_err(std::io::Error::other)?;
            remove_stale_temporary_settings(&path);
            let settings = load_settings(&handle).map_err(std::io::Error::other)?;
            app.manage(BackendProcess(Mutex::new(spawn_local_backend(&settings))));
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("failed to build Hajimi Agent desktop client");

    application.run(|app, event| {
        if matches!(event, RunEvent::Exit { .. }) {
            stop_backend(&app.state::<BackendProcess>());
        }
    });
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn validates_provider_and_model_prefix_together() {
        let input = AgentSettingsInput {
            provider: "deepseek".into(),
            model: "openai:gpt-4.1-mini".into(),
            web_search_enabled: true,
            workspace_write_enabled: true,
            agent_instructions: "Test instructions".into(),
            api_key: None,
            clear_api_key: false,
        };

        assert!(validate_settings(&input).is_err());
    }

    #[cfg(windows)]
    #[test]
    fn dpapi_secret_round_trip() {
        let secret = "test-api-key-that-must-not-be-plaintext";
        let encrypted = protect_secret(secret).expect("DPAPI encryption should succeed");

        assert!(!encrypted.contains(secret));
        assert_eq!(
            unprotect_secret(&encrypted).expect("DPAPI decryption should succeed"),
            secret
        );
    }
}
