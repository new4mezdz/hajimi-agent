#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;

use tauri::{Manager, RunEvent};

struct BackendProcess(Mutex<Option<Child>>);

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

#[cfg(debug_assertions)]
fn spawn_local_backend() -> Option<Child> {
    let (python, repository_root) = local_backend_command()?;
    let mut command = Command::new(python);
    command
        .args(["-m", "agent_product"])
        .current_dir(repository_root)
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null());

    #[cfg(target_os = "windows")]
    {
        use std::os::windows::process::CommandExt;
        command.creation_flags(0x08000000);
    }

    command.spawn().ok()
}

#[cfg(not(debug_assertions))]
fn spawn_local_backend() -> Option<Child> {
    None
}

fn main() {
    let application = tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .setup(|app| {
            app.manage(BackendProcess(Mutex::new(spawn_local_backend())));
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("failed to build Hajimi Agent desktop client");

    application.run(|app, event| {
        if matches!(event, RunEvent::Exit { .. }) {
            let backend = app.state::<BackendProcess>();
            if let Ok(mut child) = backend.0.lock() {
                if let Some(mut process) = child.take() {
                    let _ = process.kill();
                    let _ = process.wait();
                }
            };
        }
    });
}
