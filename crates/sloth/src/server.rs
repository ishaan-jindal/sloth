//! Start the local Ollama server when it is not already running.
//!
//! A server started here is detached and left running: the user may want
//! `ollama run` next, and Ollama unloads the model after a few idle minutes.

use crate::model;
use std::process::{Command, Stdio};
use std::thread::sleep;
use std::time::{Duration, Instant};

/// A single readiness probe: enough for a local HTTP reply, short enough that
/// a port with nothing listening does not stall the tool.
const PROBE_TIMEOUT: Duration = Duration::from_secs(2);
/// A cold `ollama serve` binds quickly; model loading is not part of this wait.
const READY_TIMEOUT: Duration = Duration::from_secs(30);
const POLL_INTERVAL: Duration = Duration::from_millis(250);
/// Ollama's own default; only relevant when `SLOTH_URL` omits the port.
const DEFAULT_PORT: u16 = 11434;

/// Ensure a local model server is reachable. Returns Ok(()) when the endpoint
/// answers, or an error explaining what to do when it cannot be reached.
pub fn ensure_running(base_url: &str) -> Result<(), String> {
    let base = base_url.trim_end_matches('/');
    if probe(base) {
        return Ok(());
    }

    let Some(host) = model::host_of(base) else {
        return Err(format!("cannot determine the host of {base_url:?}"));
    };
    if !model::is_loopback(host) {
        return Err(model::remote_refusal(host));
    }

    let mut command = Command::new("ollama");
    command
        .arg("serve")
        // Bind where SLOTH_URL points; a custom port therefore works too.
        .env("OLLAMA_HOST", listen_address(host, port_of(base)))
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null());
    #[cfg(unix)]
    {
        use std::os::unix::process::CommandExt;
        // Its own process group, so it outlives sloth and its terminal.
        command.process_group(0);
    }
    let child = command.spawn().map_err(|error| {
        if error.kind() == std::io::ErrorKind::NotFound {
            "the local model server is not answering and `ollama` is not installed or not on \
             PATH. Install Ollama, then start the server with `ollama serve`."
                .to_string()
        } else {
            format!(
                "the local model server is not answering and `ollama serve` could not be \
                 started: {error}. Start it yourself with `ollama serve`."
            )
        }
    })?;
    // Do not wait on it: the readiness poll below is the wait.
    drop(child);
    eprintln!("sloth: starting the local model server...");

    let deadline = Instant::now() + READY_TIMEOUT;
    loop {
        sleep(POLL_INTERVAL);
        if probe(base) {
            return Ok(());
        }
        if Instant::now() >= deadline {
            return Err(format!(
                "the local model server at {base} did not answer within {}s. Start it yourself \
                 with `ollama serve`; a server sloth started is left running so `ollama run` \
                 keeps working.",
                READY_TIMEOUT.as_secs()
            ));
        }
    }
}

/// True for any HTTP answer, not just 2xx: a reply means something is listening.
fn probe(base_url: &str) -> bool {
    let config = ureq::Agent::config_builder()
        // Same privacy rule as the chat client: never consult HTTP_PROXY.
        .proxy(None)
        .http_status_as_error(false)
        .timeout_global(Some(PROBE_TIMEOUT))
        .build();
    let agent: ureq::Agent = config.into();
    agent.get(&format!("{base_url}/api/version")).call().is_ok()
}

fn port_of(url: &str) -> u16 {
    let authority = url
        .split_once("://")
        .map_or(url, |(_, rest)| rest.split('/').next().unwrap_or(""));
    let authority = authority.rsplit('@').next().unwrap_or(authority);
    match authority.rsplit_once(':') {
        Some((_, port)) if port.chars().all(|c| c.is_ascii_digit()) && !port.is_empty() => {
            port.parse().unwrap_or(DEFAULT_PORT)
        }
        _ => DEFAULT_PORT,
    }
}

fn listen_address(host: &str, port: u16) -> String {
    if host.contains(':') {
        format!("[{host}]:{port}")
    } else {
        format!("{host}:{port}")
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Write;
    use std::net::TcpListener;
    use std::thread;

    fn base_url(listener: &TcpListener) -> String {
        format!("http://{}", listener.local_addr().expect("local addr"))
    }

    #[test]
    fn an_answering_endpoint_is_ok_and_starts_nothing() {
        let listener = TcpListener::bind("127.0.0.1:0").expect("bind a probe server");
        let url = base_url(&listener);
        let server = thread::spawn(move || {
            if let Ok((mut stream, _)) = listener.accept() {
                let body = r#"{"version":"0.0.0"}"#;
                let response = format!(
                    "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n\
                     Content-Length: {}\r\nConnection: close\r\n\r\n{body}",
                    body.len()
                );
                let _ = stream.write_all(response.as_bytes());
            }
        });

        assert!(ensure_running(&url).is_ok());
        server.join().expect("probe server thread");
    }

    #[test]
    fn a_silent_endpoint_without_ollama_names_the_manual_command() {
        // Grab a port, then free it so the probe is refused immediately.
        let listener = TcpListener::bind("127.0.0.1:0").expect("bind");
        let url = base_url(&listener);
        drop(listener);

        let empty = std::env::temp_dir().join(format!("sloth-empty-path-{}", std::process::id()));
        std::fs::create_dir_all(&empty).expect("empty PATH dir");
        let previous = std::env::var_os("PATH");
        // No `ollama` binary can be found on an empty PATH.
        std::env::set_var("PATH", &empty);
        let result = ensure_running(&url);
        match previous {
            Some(value) => std::env::set_var("PATH", value),
            None => std::env::remove_var("PATH"),
        }
        let _ = std::fs::remove_dir(&empty);

        let error = result.expect_err("nothing is listening and ollama is unreachable");
        assert!(error.contains("ollama serve"), "{error}");
    }

    #[test]
    fn a_non_loopback_endpoint_is_not_started_and_keeps_the_opt_in() {
        let error = ensure_running("http://192.0.2.1:11434").expect_err("reserved test address");
        assert!(error.contains("SLOTH_ALLOW_REMOTE=1"), "{error}");
        assert!(!error.contains("ollama serve"), "{error}");
    }
}
