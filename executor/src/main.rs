use serde::de::{DeserializeSeed, MapAccess, SeqAccess, Visitor};
use serde::{Deserialize, Serialize};
use std::collections::HashSet;
use std::env;
use std::fs;
use std::io::{self, BufRead, Read, Write};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::thread;

#[cfg(windows)]
use std::os::windows::io::AsRawHandle;
use std::time::{Duration, Instant};
#[cfg(windows)]
use windows_sys::Win32::Foundation::{CloseHandle, HANDLE};
#[cfg(windows)]
use windows_sys::Win32::System::JobObjects::{
    AssignProcessToJobObject, CreateJobObjectW, JobObjectExtendedLimitInformation,
    SetInformationJobObject, JOBOBJECT_EXTENDED_LIMIT_INFORMATION,
    JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
};

const PROTOCOL_VERSION: u32 = 1;
const MAX_OPERATION_ID: usize = 200;
const MAX_ARGS: usize = 128;
const MAX_ARG_LENGTH: usize = 8_192;
const MAX_TOTAL_ARGS: usize = 65_536;
const MAX_TIMEOUT_MS: u64 = 1_800_000;
const DEFAULT_OUTPUT_BYTES: usize = 65_536;
const MAX_OUTPUT_BYTES: usize = 1_048_576;
const MAX_SEEN_OPERATION_IDS: usize = 4_096;
const MAX_IDENTIFIER_BYTES: usize = 200;
const MAX_REQUEST_LINE_BYTES: usize = 2 * 1024 * 1024;
const MIN_MEMORY_BYTES: u64 = 16 * 1024 * 1024;
const MAX_MEMORY_BYTES: u64 = 4 * 1024 * 1024 * 1024;
const ALLOWED_OPERATIONS: &[&str] = &[
    "probe",
    "extract_audio",
    "extract_frames",
    "detect_duration",
    "trim",
    "concat",
    "crop",
    "resize",
    "normalize_audio",
    "add_subtitles",
    "transcode",
    "thumbnail",
    "checksum",
];

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct ExecuteRequest {
    protocol_version: u32,
    auth_token: String,
    operation_id: String,
    operation: String,
    trace_id: Option<String>,
    project_id: Option<String>,
    max_memory_bytes: Option<u64>,
    max_cpu_time_ms: Option<u64>,
    binary: String,
    args: Vec<String>,
    input_paths: Vec<String>,
    output_paths: Vec<String>,
    allowed_roots: Vec<String>,
    binary_roots: Vec<String>,
    timeout_ms: u64,
    #[serde(default = "default_output_bytes")]
    max_output_bytes: usize,
}

fn default_output_bytes() -> usize {
    DEFAULT_OUTPUT_BYTES
}

#[derive(Debug, Serialize, Deserialize)]
struct ExecuteResponse {
    protocol_version: u32,
    operation_id: String,
    operation: String,
    trace_id: Option<String>,
    project_id: Option<String>,
    status: String,
    error_code: Option<String>,
    message: String,
    exit_code: Option<i32>,
    stdout: String,
    stderr: String,
    duration_ms: u128,
}

#[cfg(windows)]
struct ProcessTreeGuard {
    handle: HANDLE,
}

#[cfg(windows)]
impl ProcessTreeGuard {
    fn attach(
        child: &Child,
        max_memory_bytes: Option<u64>,
        max_cpu_time_ms: Option<u64>,
    ) -> Result<Self, String> {
        // Closing a kill-on-close Job Object terminates the complete descendant tree.
        let handle = unsafe { CreateJobObjectW(std::ptr::null(), std::ptr::null()) };
        if handle.is_null() {
            return Err("Windows Job Object creation failed.".to_string());
        }
        let mut limits: JOBOBJECT_EXTENDED_LIMIT_INFORMATION = unsafe { std::mem::zeroed() };
        limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
        if let Some(memory_bytes) = max_memory_bytes {
            limits.BasicLimitInformation.LimitFlags |=
                windows_sys::Win32::System::JobObjects::JOB_OBJECT_LIMIT_PROCESS_MEMORY;
            limits.ProcessMemoryLimit = memory_bytes as usize;
        }
        if let Some(cpu_time_ms) = max_cpu_time_ms {
            limits.BasicLimitInformation.LimitFlags |=
                windows_sys::Win32::System::JobObjects::JOB_OBJECT_LIMIT_PROCESS_TIME;
            limits.BasicLimitInformation.PerProcessUserTimeLimit = (cpu_time_ms * 10_000) as i64;
        }
        let configured = unsafe {
            SetInformationJobObject(
                handle,
                JobObjectExtendedLimitInformation,
                &mut limits as *mut _ as *mut std::ffi::c_void,
                std::mem::size_of::<JOBOBJECT_EXTENDED_LIMIT_INFORMATION>() as u32,
            )
        };
        if configured == 0 {
            unsafe { CloseHandle(handle) };
            return Err("Windows Job Object limit configuration failed.".to_string());
        }
        let assigned = unsafe { AssignProcessToJobObject(handle, child.as_raw_handle() as HANDLE) };
        if assigned == 0 {
            unsafe { CloseHandle(handle) };
            return Err("The child process could not be assigned to its Job Object.".to_string());
        }
        Ok(Self { handle })
    }
}

#[cfg(windows)]
impl Drop for ProcessTreeGuard {
    fn drop(&mut self) {
        unsafe { CloseHandle(self.handle) };
    }
}

#[cfg(not(windows))]
struct ProcessTreeGuard;

#[cfg(not(windows))]
impl ProcessTreeGuard {
    fn attach(
        _child: &Child,
        _max_memory_bytes: Option<u64>,
        _max_cpu_time_ms: Option<u64>,
    ) -> Result<Self, String> {
        Ok(Self)
    }
}

// This constructor keeps response-field assembly centralized for every protocol branch.
#[allow(clippy::too_many_arguments)]
fn response(
    request: &ExecuteRequest,
    status: &str,
    code: Option<&str>,
    message: String,
    exit_code: Option<i32>,
    stdout: String,
    stderr: String,
    started: Instant,
) -> ExecuteResponse {
    let limit = request.max_output_bytes.clamp(1, MAX_OUTPUT_BYTES);
    ExecuteResponse {
        protocol_version: PROTOCOL_VERSION,
        operation_id: request.operation_id.clone(),
        operation: request.operation.clone(),
        trace_id: request.trace_id.clone(),
        project_id: request.project_id.clone(),
        status: status.to_string(),
        error_code: code.map(str::to_string),
        message,
        exit_code,
        stdout: truncate(&stdout, limit),
        stderr: truncate(&stderr, limit),
        duration_ms: started.elapsed().as_millis(),
    }
}

fn truncate(value: &str, max_bytes: usize) -> String {
    let mut result = String::new();
    for character in value.chars() {
        if result.len() + character.len_utf8() > max_bytes {
            break;
        }
        result.push(character);
    }
    result
}

fn canonical_existing(path: &str) -> Result<PathBuf, String> {
    fs::canonicalize(path).map_err(|_| format!("Path does not exist: {path}"))
}

fn canonical_parent(path: &str) -> Result<PathBuf, String> {
    let target = Path::new(path);
    let parent = target
        .parent()
        .ok_or_else(|| format!("Output has no parent: {path}"))?;
    let filename = target
        .file_name()
        .ok_or_else(|| format!("Output has no filename: {path}"))?;
    if filename.to_string_lossy().contains('\0') {
        return Err(format!(
            "Output filename contains an invalid character: {path}"
        ));
    }
    let canonical_parent = fs::canonicalize(parent)
        .map_err(|_| format!("Output parent does not exist: {parent:?}"))?;
    Ok(canonical_parent.join(filename))
}

fn canonical_roots(values: &[String]) -> Vec<PathBuf> {
    values
        .iter()
        .filter_map(|root| fs::canonicalize(root).ok())
        .collect()
}

fn inside(path: &Path, roots: &[PathBuf]) -> bool {
    roots
        .iter()
        .any(|root| path == root || path.starts_with(root))
}

fn constant_time_equal(left: &[u8], right: &[u8]) -> bool {
    let mut difference = left.len() ^ right.len();
    for index in 0..left.len().max(right.len()) {
        difference |= left.get(index).copied().unwrap_or_default() as usize
            ^ right.get(index).copied().unwrap_or_default() as usize;
    }
    difference == 0
}

fn valid_identifier(value: &str) -> bool {
    !value.is_empty()
        && value.len() <= MAX_IDENTIFIER_BYTES
        && value
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || b"_.:-".contains(&byte))
}

struct StrictJsonSeed;

struct StrictJsonVisitor;

impl<'de> Visitor<'de> for StrictJsonVisitor {
    type Value = ();

    fn expecting(&self, formatter: &mut std::fmt::Formatter) -> std::fmt::Result {
        formatter.write_str("valid JSON without duplicate object keys")
    }

    fn visit_map<A>(self, mut access: A) -> Result<Self::Value, A::Error>
    where
        A: MapAccess<'de>,
    {
        let mut keys = HashSet::new();
        while let Some(key) = access.next_key::<String>()? {
            if !keys.insert(key.clone()) {
                return Err(serde::de::Error::custom(format!(
                    "duplicate JSON key: {key}"
                )));
            }
            access.next_value_seed(StrictJsonSeed)?;
        }
        Ok(())
    }

    fn visit_seq<A>(self, mut access: A) -> Result<Self::Value, A::Error>
    where
        A: SeqAccess<'de>,
    {
        while access.next_element_seed(StrictJsonSeed)?.is_some() {}
        Ok(())
    }

    fn visit_bool<E>(self, _value: bool) -> Result<Self::Value, E>
    where
        E: serde::de::Error,
    {
        Ok(())
    }
    fn visit_i64<E>(self, _value: i64) -> Result<Self::Value, E>
    where
        E: serde::de::Error,
    {
        Ok(())
    }
    fn visit_u64<E>(self, _value: u64) -> Result<Self::Value, E>
    where
        E: serde::de::Error,
    {
        Ok(())
    }
    fn visit_f64<E>(self, _value: f64) -> Result<Self::Value, E>
    where
        E: serde::de::Error,
    {
        Ok(())
    }
    fn visit_str<E>(self, _value: &str) -> Result<Self::Value, E>
    where
        E: serde::de::Error,
    {
        Ok(())
    }
    fn visit_string<E>(self, _value: String) -> Result<Self::Value, E>
    where
        E: serde::de::Error,
    {
        Ok(())
    }
    fn visit_none<E>(self) -> Result<Self::Value, E>
    where
        E: serde::de::Error,
    {
        Ok(())
    }
    fn visit_unit<E>(self) -> Result<Self::Value, E>
    where
        E: serde::de::Error,
    {
        Ok(())
    }
}

impl<'de> DeserializeSeed<'de> for StrictJsonSeed {
    type Value = ();

    fn deserialize<D>(self, deserializer: D) -> Result<Self::Value, D::Error>
    where
        D: serde::Deserializer<'de>,
    {
        deserializer.deserialize_any(StrictJsonVisitor)
    }
}

fn reject_duplicate_json_keys(line: &str) -> Result<(), String> {
    let mut deserializer = serde_json::Deserializer::from_str(line);
    StrictJsonSeed
        .deserialize(&mut deserializer)
        .map_err(|error| error.to_string())?;
    deserializer.end().map_err(|error| error.to_string())
}

fn validate_args(args: &[String]) -> Result<(), String> {
    if args.len() > MAX_ARGS {
        return Err("The argument count exceeds the executor limit.".to_string());
    }
    let mut total = 0usize;
    for argument in args {
        if argument.len() > MAX_ARG_LENGTH || argument.contains('\0') {
            return Err("An argument is too long or contains an invalid character.".to_string());
        }
        let lowered = argument.to_ascii_lowercase();
        if lowered.contains("://")
            || matches!(
                lowered.as_str(),
                "-protocol_whitelist" | "-protocol_blacklist" | "-allowed_protocols"
            )
        {
            return Err(
                "Network input and protocol-control arguments are forbidden by default."
                    .to_string(),
            );
        }
        total = total.saturating_add(argument.len());
        if total > MAX_TOTAL_ARGS {
            return Err("The total argument size exceeds the executor limit.".to_string());
        }
    }
    Ok(())
}

fn read_limited<R: Read>(mut reader: R, max_bytes: usize) -> String {
    let mut retained = Vec::new();
    let mut buffer = [0u8; 8192];
    loop {
        match reader.read(&mut buffer) {
            Ok(0) => break,
            Ok(count) => {
                if retained.len() < max_bytes {
                    let remaining = max_bytes - retained.len();
                    retained.extend_from_slice(&buffer[..count.min(remaining)]);
                }
            }
            Err(_) => break,
        }
    }
    String::from_utf8_lossy(&retained).to_string()
}

fn collect_output(child: &mut Child, max_bytes: usize) -> (String, String) {
    let stdout = child.stdout.take();
    let stderr = child.stderr.take();
    let stdout_thread = thread::spawn(move || {
        stdout
            .map(|stream| read_limited(stream, max_bytes))
            .unwrap_or_default()
    });
    let stderr_thread = thread::spawn(move || {
        stderr
            .map(|stream| read_limited(stream, max_bytes))
            .unwrap_or_default()
    });
    let stdout_value = stdout_thread.join().unwrap_or_default();
    let stderr_value = stderr_thread.join().unwrap_or_default();
    (stdout_value, stderr_value)
}

type ValidationResult = Result<(PathBuf, Vec<PathBuf>, Vec<PathBuf>), (&'static str, String)>;

fn validate_request(request: &ExecuteRequest) -> ValidationResult {
    if request.protocol_version != PROTOCOL_VERSION {
        return Err((
            "EXECUTOR_PROTOCOL_UNSUPPORTED",
            "The executor protocol version is unsupported.".to_string(),
        ));
    }
    let expected_token = env::var("FLUJO_EXECUTOR_TOKEN").unwrap_or_default();
    if expected_token.is_empty() {
        return Err((
            "EXECUTOR_AUTH_UNAVAILABLE",
            "The secure executor parent token is unavailable.".to_string(),
        ));
    }
    if !constant_time_equal(request.auth_token.as_bytes(), expected_token.as_bytes()) {
        return Err((
            "EXECUTOR_AUTH_FAILED",
            "The secure executor parent authentication failed.".to_string(),
        ));
    }
    if !valid_identifier(&request.operation_id) || request.operation_id.len() > MAX_OPERATION_ID {
        return Err((
            "INVALID_OPERATION_ID",
            "Operation ID is missing or too long.".to_string(),
        ));
    }
    if request
        .trace_id
        .as_deref()
        .is_some_and(|value| !valid_identifier(value))
        || request
            .project_id
            .as_deref()
            .is_some_and(|value| !valid_identifier(value))
    {
        return Err((
            "INVALID_REQUEST_IDENTITY",
            "Trace and project identities are malformed.".to_string(),
        ));
    }
    if !ALLOWED_OPERATIONS.contains(&request.operation.as_str()) {
        return Err((
            "EXECUTOR_OPERATION_FORBIDDEN",
            "The media operation is not allowlisted.".to_string(),
        ));
    }
    if request.input_paths.len() > MAX_ARGS || request.output_paths.len() > MAX_ARGS {
        return Err((
            "EXECUTOR_PATH_COUNT_INVALID",
            "The path count exceeds the executor limit.".to_string(),
        ));
    }
    validate_args(&request.args).map_err(|message| ("EXECUTOR_ARGUMENTS_INVALID", message))?;
    if request.timeout_ms == 0 || request.timeout_ms > MAX_TIMEOUT_MS {
        return Err((
            "EXECUTOR_TIMEOUT_INVALID",
            "Timeout is outside the permitted range.".to_string(),
        ));
    }
    if request.max_output_bytes == 0 || request.max_output_bytes > MAX_OUTPUT_BYTES {
        return Err((
            "EXECUTOR_OUTPUT_LIMIT_INVALID",
            "Output bound is outside the permitted range.".to_string(),
        ));
    }
    if let Some(memory_bytes) = request.max_memory_bytes {
        if !(MIN_MEMORY_BYTES..=MAX_MEMORY_BYTES).contains(&memory_bytes) {
            return Err((
                "EXECUTOR_MEMORY_LIMIT_INVALID",
                "Memory bound is outside the permitted range.".to_string(),
            ));
        }
    }
    if let Some(cpu_time_ms) = request.max_cpu_time_ms {
        if cpu_time_ms == 0 || cpu_time_ms > request.timeout_ms {
            return Err((
                "EXECUTOR_CPU_LIMIT_INVALID",
                "CPU-time bound is outside the permitted range.".to_string(),
            ));
        }
    }
    let binary_name = Path::new(&request.binary)
        .file_name()
        .and_then(|name| name.to_str())
        .unwrap_or("")
        .to_ascii_lowercase();
    if binary_name != "ffmpeg.exe" && binary_name != "ffprobe.exe" {
        return Err((
            "EXECUTOR_BINARY_FORBIDDEN",
            "Only the bundled FFmpeg and FFprobe binaries are allowed.".to_string(),
        ));
    }
    let binary_path = canonical_existing(&request.binary)
        .map_err(|message| ("EXECUTOR_BINARY_MISSING", message))?;
    let binary_roots = canonical_roots(&request.binary_roots);
    if binary_roots.is_empty() || !inside(&binary_path, &binary_roots) {
        return Err((
            "EXECUTOR_BINARY_PATH_FORBIDDEN",
            "The binary is outside the approved bundled media root.".to_string(),
        ));
    }
    let roots = canonical_roots(&request.allowed_roots);
    if roots.is_empty() {
        return Err((
            "EXECUTOR_ROOTS_MISSING",
            "No canonical allowed roots were supplied.".to_string(),
        ));
    }
    for input in &request.input_paths {
        let path =
            canonical_existing(input).map_err(|message| ("EXECUTOR_INPUT_INVALID", message))?;
        if !inside(&path, &roots) {
            return Err((
                "EXECUTOR_PATH_FORBIDDEN",
                format!("Input is outside the allowed roots: {input}"),
            ));
        }
    }
    for output in &request.output_paths {
        let path =
            canonical_parent(output).map_err(|message| ("EXECUTOR_OUTPUT_INVALID", message))?;
        if !inside(&path, &roots) {
            return Err((
                "EXECUTOR_PATH_FORBIDDEN",
                format!("Output is outside the allowed roots: {output}"),
            ));
        }
    }
    Ok((binary_path, roots, binary_roots))
}

fn duplicate_response(request: &ExecuteRequest) -> ExecuteResponse {
    response(
        request,
        "blocked",
        Some("EXECUTOR_NONCE_REUSED"),
        "The executor operation nonce was already consumed and cannot be replayed.".to_string(),
        None,
        String::new(),
        String::new(),
        Instant::now(),
    )
}

fn output_exceeds_limit(paths: &[String], max_bytes: usize) -> bool {
    paths.iter().any(|path| {
        fs::metadata(path)
            .map(|metadata| metadata.len() > max_bytes as u64)
            .unwrap_or(false)
    })
}

fn process_line(line: &str, seen_operation_ids: &mut HashSet<String>) -> String {
    if line.len() > MAX_REQUEST_LINE_BYTES {
        return "{\"protocol_version\":1,\"status\":\"blocked\",\"error_code\":\"EXECUTOR_REQUEST_TOO_LARGE\",\"message\":\"The executor request exceeds the protocol size limit.\"}".to_string();
    }
    if let Err(error) = reject_duplicate_json_keys(line) {
        return format!(
            "{{\"protocol_version\":1,\"status\":\"blocked\",\"error_code\":\"EXECUTOR_DUPLICATE_KEY\",\"message\":{}}}",
            serde_json::to_string(&error).unwrap_or_else(|_| "\"Duplicate JSON key.\"".to_string())
        );
    }
    match serde_json::from_str::<ExecuteRequest>(line) {
        Ok(request) => {
            let valid = validate_request(&request).is_ok();
            let duplicate = valid && seen_operation_ids.contains(&request.operation_id);
            if valid && !duplicate && seen_operation_ids.len() >= MAX_SEEN_OPERATION_IDS {
                return serde_json::to_string(&response(
                    &request,
                    "blocked",
                    Some("EXECUTOR_REPLAY_WINDOW_FULL"),
                    "The executor replay-protection window is full; restart is required before accepting new identities.".to_string(),
                    None,
                    String::new(),
                    String::new(),
                    Instant::now(),
                )).unwrap_or_else(|_| "{\"protocol_version\":1,\"status\":\"blocked\",\"error_code\":\"SERIALIZATION_FAILED\"}".to_string());
            }
            if valid && !duplicate {
                seen_operation_ids.insert(request.operation_id.clone());
            }
            if duplicate {
                serde_json::to_string(&duplicate_response(&request)).unwrap_or_else(|_| "{\"protocol_version\":1,\"status\":\"failed\",\"error_code\":\"SERIALIZATION_FAILED\"}".to_string())
            } else {
                serde_json::to_string(&handle(request)).unwrap_or_else(|_| "{\"protocol_version\":1,\"status\":\"failed\",\"error_code\":\"SERIALIZATION_FAILED\"}".to_string())
            }
        }
        Err(error) => format!(
            "{{\"protocol_version\":1,\"status\":\"blocked\",\"error_code\":\"INVALID_REQUEST\",\"message\":{}}}",
            serde_json::to_string(&error.to_string()).unwrap()
        ),
    }
}

fn handle(request: ExecuteRequest) -> ExecuteResponse {
    let started = Instant::now();
    let max_output_bytes = request.max_output_bytes.clamp(1, MAX_OUTPUT_BYTES);
    match validate_request(&request) {
        Ok((binary_path, roots, _binary_roots)) => {
            let mut command = Command::new(binary_path);
            command
                .args(&request.args)
                .stdin(Stdio::null())
                .stdout(Stdio::piped())
                .stderr(Stdio::piped())
                .current_dir(&roots[0])
                .env_clear();
            if let Ok(system_root) = env::var("SystemRoot") {
                command.env("SystemRoot", system_root);
            }
            let mut child = match command.spawn() {
                Ok(child) => child,
                Err(error) => {
                    return response(
                        &request,
                        "failed",
                        Some("EXECUTOR_SPAWN_FAILED"),
                        error.to_string(),
                        None,
                        String::new(),
                        String::new(),
                        started,
                    )
                }
            };
            let _process_tree = match ProcessTreeGuard::attach(
                &child,
                request.max_memory_bytes,
                request.max_cpu_time_ms,
            ) {
                Ok(guard) => guard,
                Err(error) => {
                    let _ = child.kill();
                    let _ = child.wait();
                    return response(
                        &request,
                        "blocked",
                        Some("EXECUTOR_JOB_ASSIGN_FAILED"),
                        error,
                        None,
                        String::new(),
                        String::new(),
                        started,
                    );
                }
            };
            let deadline = Instant::now() + Duration::from_millis(request.timeout_ms);
            loop {
                if output_exceeds_limit(&request.output_paths, max_output_bytes) {
                    let _ = child.kill();
                    let status = child.wait().ok();
                    let (stdout, stderr) = collect_output(&mut child, max_output_bytes);
                    return response(
                        &request,
                        "blocked",
                        Some("EXECUTOR_OUTPUT_LIMIT_EXCEEDED"),
                        "The child process exceeded the bounded output-file limit.".to_string(),
                        status.and_then(|value| value.code()),
                        stdout,
                        stderr,
                        started,
                    );
                }
                match child.try_wait() {
                    Ok(Some(status)) => {
                        let (stdout, stderr) = collect_output(&mut child, max_output_bytes);
                        let success = status.success();
                        return response(
                            &request,
                            if success { "completed" } else { "failed" },
                            if success {
                                None
                            } else {
                                Some("EXECUTOR_TOOL_FAILED")
                            },
                            if success {
                                "Typed tool operation completed.".to_string()
                            } else {
                                "Typed tool operation failed.".to_string()
                            },
                            status.code(),
                            stdout,
                            stderr,
                            started,
                        );
                    }
                    Ok(None) if Instant::now() < deadline => {
                        thread::sleep(Duration::from_millis(25))
                    }
                    Ok(None) => {
                        let _ = child.kill();
                        let status = child.wait().ok();
                        let (stdout, stderr) = collect_output(&mut child, max_output_bytes);
                        return response(
                            &request,
                            "blocked",
                            Some("EXECUTOR_TIMEOUT"),
                            "Typed tool operation exceeded its time limit.".to_string(),
                            status.and_then(|value| value.code()),
                            stdout,
                            stderr,
                            started,
                        );
                    }
                    Err(error) => {
                        let _ = child.kill();
                        let _ = child.wait();
                        return response(
                            &request,
                            "failed",
                            Some("EXECUTOR_WAIT_FAILED"),
                            error.to_string(),
                            None,
                            String::new(),
                            String::new(),
                            started,
                        );
                    }
                }
            }
        }
        Err((code, message)) => response(
            &request,
            "blocked",
            Some(code),
            message,
            None,
            String::new(),
            String::new(),
            started,
        ),
    }
}

#[cfg(test)]
#[allow(clippy::items_after_test_module)]
mod tests {
    use super::*;
    use std::fs;
    use std::sync::Mutex;
    use std::time::{SystemTime, UNIX_EPOCH};

    static ENV_LOCK: Mutex<()> = Mutex::new(());

    fn unique_dir(label: &str) -> PathBuf {
        let stamp = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .expect("clock should be available")
            .as_nanos();
        let path = env::temp_dir().join(format!("flujo-executor-{label}-{stamp}"));
        fs::create_dir_all(&path).expect("temporary test directory should be created");
        path
    }

    fn request(root: &Path, binary: &Path, input: &Path) -> ExecuteRequest {
        ExecuteRequest {
            protocol_version: PROTOCOL_VERSION,
            auth_token: "test-token".to_string(),
            operation_id: "test-operation".to_string(),
            operation: "probe".to_string(),
            trace_id: Some("test-trace".to_string()),
            project_id: Some("test-project".to_string()),
            max_memory_bytes: Some(64 * 1024 * 1024),
            max_cpu_time_ms: Some(500),
            binary: binary.to_string_lossy().to_string(),
            args: vec!["-version".to_string()],
            input_paths: vec![input.to_string_lossy().to_string()],
            output_paths: Vec::new(),
            allowed_roots: vec![root.to_string_lossy().to_string()],
            binary_roots: vec![binary.parent().unwrap().to_string_lossy().to_string()],
            timeout_ms: 1000,
            max_output_bytes: DEFAULT_OUTPUT_BYTES,
        }
    }

    #[test]
    fn truncation_respects_utf8_boundaries() {
        assert_eq!(truncate("abcé", 4), "abc");
        assert_eq!(truncate("abcé", 5), "abcé");
    }

    #[test]
    fn bounded_reader_discards_bytes_after_limit() {
        let output = read_limited("0123456789".as_bytes(), 4);
        assert_eq!(output, "0123");
    }

    #[cfg(windows)]
    #[test]
    fn timeout_kills_allowlisted_ffmpeg_process() {
        let binary = env::current_dir()
            .expect("repository directory should be available")
            .join("resources")
            .join("media")
            .join("windows-x64")
            .join("ffmpeg.exe");
        if !binary.is_file() {
            return;
        }
        let root = unique_dir("timeout");
        let request = ExecuteRequest {
            protocol_version: PROTOCOL_VERSION,
            auth_token: "test-token".to_string(),
            operation_id: "timeout-operation".to_string(),
            operation: "transcode".to_string(),
            trace_id: Some("timeout-trace".to_string()),
            project_id: Some("timeout-project".to_string()),
            max_memory_bytes: Some(64 * 1024 * 1024),
            max_cpu_time_ms: Some(900),
            binary: binary.to_string_lossy().to_string(),
            args: vec![
                "-re".to_string(),
                "-f".to_string(),
                "lavfi".to_string(),
                "-i".to_string(),
                "color=c=blue:s=16x16:r=1".to_string(),
                "-t".to_string(),
                "30".to_string(),
                "-f".to_string(),
                "null".to_string(),
                "-".to_string(),
            ],
            input_paths: Vec::new(),
            output_paths: Vec::new(),
            allowed_roots: vec![root.to_string_lossy().to_string()],
            binary_roots: vec![binary.parent().unwrap().to_string_lossy().to_string()],
            timeout_ms: 1000,
            max_output_bytes: DEFAULT_OUTPUT_BYTES,
        };
        let _lock = ENV_LOCK
            .lock()
            .expect("environment lock should be available");
        unsafe { env::set_var("FLUJO_EXECUTOR_TOKEN", "test-token") };
        let response = handle(request);
        unsafe { env::remove_var("FLUJO_EXECUTOR_TOKEN") };
        let _ = fs::remove_dir_all(root);
        assert_eq!(response.error_code.as_deref(), Some("EXECUTOR_TIMEOUT"));
        assert_eq!(response.status, "blocked");
    }

    #[cfg(windows)]
    #[test]
    fn concurrent_timeout_operations_are_bounded() {
        let _lock = ENV_LOCK
            .lock()
            .expect("environment lock should be available");
        unsafe { env::set_var("FLUJO_EXECUTOR_TOKEN", "test-token") };
        let root = unique_dir("timeout-load");
        let binary = env::current_dir()
            .expect("repository directory should be available")
            .join("resources")
            .join("media")
            .join("windows-x64")
            .join("ffmpeg.exe");
        if !binary.is_file() {
            unsafe { env::remove_var("FLUJO_EXECUTOR_TOKEN") };
            let _ = fs::remove_dir_all(root);
            return;
        }
        let requests: Vec<ExecuteRequest> = (0..4)
            .map(|index| ExecuteRequest {
                protocol_version: PROTOCOL_VERSION,
                auth_token: "test-token".to_string(),
                operation_id: format!("timeout-load-{index}"),
                operation: "transcode".to_string(),
                trace_id: Some(format!("timeout-load-trace-{index}")),
                project_id: Some("timeout-load-project".to_string()),
                max_memory_bytes: Some(64 * 1024 * 1024),
                max_cpu_time_ms: Some(450),
                binary: binary.to_string_lossy().to_string(),
                args: vec![
                    "-re".to_string(),
                    "-f".to_string(),
                    "lavfi".to_string(),
                    "-i".to_string(),
                    "color=c=blue:s=16x16:r=1".to_string(),
                    "-t".to_string(),
                    "30".to_string(),
                    "-f".to_string(),
                    "null".to_string(),
                    "-".to_string(),
                ],
                input_paths: Vec::new(),
                output_paths: Vec::new(),
                allowed_roots: vec![root.to_string_lossy().to_string()],
                binary_roots: vec![binary.parent().unwrap().to_string_lossy().to_string()],
                timeout_ms: 500,
                max_output_bytes: DEFAULT_OUTPUT_BYTES,
            })
            .collect();
        let handles: Vec<_> = requests
            .into_iter()
            .map(|request| thread::spawn(move || handle(request)))
            .collect();
        for worker in handles {
            let result = worker.join().expect("timeout worker should join");
            assert_eq!(result.status, "blocked");
            assert_eq!(result.error_code.as_deref(), Some("EXECUTOR_TIMEOUT"));
        }
        unsafe { env::remove_var("FLUJO_EXECUTOR_TOKEN") };
        let _ = fs::remove_dir_all(root);
    }

    #[test]
    fn bounded_reader_stress_discards_large_output() {
        let source = vec![b'x'; MAX_OUTPUT_BYTES * 2];
        let output = read_limited(source.as_slice(), MAX_OUTPUT_BYTES);
        assert_eq!(output.len(), MAX_OUTPUT_BYTES);
    }

    #[test]
    fn argument_limits_reject_oversized_and_nul_values() {
        assert!(validate_args(&["a".repeat(MAX_ARG_LENGTH + 1)]).is_err());
        assert!(validate_args(&["bad\0arg".to_string()]).is_err());
        assert!(validate_args(&["-version".to_string()]).is_ok());
    }

    #[test]
    fn request_validation_rejects_auth_binary_and_path_escape() {
        let _lock = ENV_LOCK
            .lock()
            .expect("environment lock should be available");
        unsafe { env::set_var("FLUJO_EXECUTOR_TOKEN", "test-token") };
        let root = unique_dir("validation");
        let media = root.join("media");
        fs::create_dir_all(&media).expect("media directory should be created");
        let binary = media.join("ffmpeg.exe");
        let input = root.join("input.mp4");
        fs::write(&binary, b"test-binary").expect("binary fixture should be written");
        fs::write(&input, b"test-input").expect("input fixture should be written");

        let valid = request(&root, &binary, &input);
        assert!(validate_request(&valid).is_ok());

        let mut wrong_auth = valid.clone();
        wrong_auth.auth_token = "wrong".to_string();
        assert_eq!(
            validate_request(&wrong_auth).unwrap_err().0,
            "EXECUTOR_AUTH_FAILED"
        );

        let mut forbidden_binary = valid.clone();
        forbidden_binary.binary = root.join("not-allowed.exe").to_string_lossy().to_string();
        fs::write(&forbidden_binary.binary, b"x")
            .expect("forbidden binary fixture should be written");
        assert_eq!(
            validate_request(&forbidden_binary).unwrap_err().0,
            "EXECUTOR_BINARY_FORBIDDEN"
        );

        let outside = unique_dir("outside").join("outside.mp4");
        fs::write(&outside, b"outside").expect("outside fixture should be written");
        let mut path_escape = valid;
        path_escape.input_paths = vec![outside.to_string_lossy().to_string()];
        assert_eq!(
            validate_request(&path_escape).unwrap_err().0,
            "EXECUTOR_PATH_FORBIDDEN"
        );

        let _ = fs::remove_dir_all(root);
        let _ = fs::remove_dir_all(outside.parent().unwrap());
        unsafe { env::remove_var("FLUJO_EXECUTOR_TOKEN") };
    }

    #[cfg(windows)]
    #[test]
    fn job_object_terminates_descendant_tree() {
        let root = unique_dir("job");
        let marker = root.join("descendant-finished.txt");
        let command = format!(
            "ping 127.0.0.1 -n 30 > nul & echo finished > \"{}\"",
            marker.display()
        );
        let mut child = Command::new("cmd.exe")
            .args(["/d", "/c", &command])
            .spawn()
            .expect("Windows command fixture should spawn");
        let guard = ProcessTreeGuard::attach(&child, Some(64 * 1024 * 1024), Some(1000))
            .expect("child should join a Job Object");
        thread::sleep(Duration::from_millis(250));
        drop(guard);
        let _ = child.wait();
        assert!(
            !marker.exists(),
            "the descendant should be terminated before it can write the marker"
        );
        let _ = fs::remove_dir_all(root);
    }

    #[cfg(windows)]
    #[test]
    fn job_object_terminates_nested_descendant_tree() {
        let root = unique_dir("nested-job");
        let marker = root.join("nested-descendant-finished.txt");
        let command = format!(
            "cmd.exe /d /c ping 127.0.0.1 -n 30 > nul & echo finished > \"{}\"",
            marker.display()
        );
        let mut child = Command::new("cmd.exe")
            .args(["/d", "/c", &command])
            .spawn()
            .expect("nested Windows command fixture should spawn");
        let guard = ProcessTreeGuard::attach(&child, Some(64 * 1024 * 1024), Some(1000))
            .expect("nested child should join a Job Object");
        thread::sleep(Duration::from_millis(250));
        drop(guard);
        let _ = child.wait();
        assert!(
            !marker.exists(),
            "nested descendants should be terminated before they can write the marker"
        );
        let _ = fs::remove_dir_all(root);
    }

    #[test]
    fn malformed_json_is_rejected_before_execution() {
        let malformed = serde_json::from_str::<ExecuteRequest>("{not-json");
        assert!(malformed.is_err());
        let missing_field =
            serde_json::from_str::<ExecuteRequest>(r#"{"protocol_version":1,"auth_token":"x"}"#);
        assert!(missing_field.is_err());
        let unknown_field = serde_json::from_str::<ExecuteRequest>(
            r#"{"protocol_version":1,"auth_token":"x","unexpected":true}"#,
        );
        assert!(unknown_field.is_err());
    }

    #[test]
    fn persistent_loop_rejects_operation_nonce_reuse() {
        let _lock = ENV_LOCK
            .lock()
            .expect("environment lock should be available");
        unsafe { env::set_var("FLUJO_EXECUTOR_TOKEN", "test-token") };
        let root = unique_dir("nonce");
        let media = root.join("media");
        fs::create_dir_all(&media).expect("media directory should be created");
        let binary = media.join("ffprobe.exe");
        let input = root.join("input.mp4");
        fs::write(&binary, b"test-binary").expect("binary fixture should be written");
        fs::write(&input, b"test-input").expect("test input should be written");
        let request = request(&root, &binary, &input);
        let line = serde_json::to_string(&request).expect("request should serialize");
        let mut seen = HashSet::new();
        let first: ExecuteResponse = serde_json::from_str(&process_line(&line, &mut seen))
            .expect("first response should parse");
        let second: ExecuteResponse = serde_json::from_str(&process_line(&line, &mut seen))
            .expect("duplicate response should parse");
        assert_ne!(first.error_code.as_deref(), Some("EXECUTOR_NONCE_REUSED"));
        assert_eq!(second.error_code.as_deref(), Some("EXECUTOR_NONCE_REUSED"));
        assert_eq!(second.status, "blocked");
        let _ = fs::remove_dir_all(root);
        unsafe { env::remove_var("FLUJO_EXECUTOR_TOKEN") };
    }

    #[cfg(windows)]
    #[test]
    fn junction_escape_is_rejected_when_windows_allows_fixture_creation() {
        let _lock = ENV_LOCK
            .lock()
            .expect("environment lock should be available");
        unsafe { env::set_var("FLUJO_EXECUTOR_TOKEN", "test-token") };
        let root = unique_dir("junction");
        let media = root.join("media");
        fs::create_dir_all(&media).expect("media directory should be created");
        let binary = media.join("ffprobe.exe");
        let input = root.join("input.mp4");
        fs::write(&binary, b"test-binary").expect("binary fixture should be written");
        fs::write(&input, b"test-input").expect("test input should be written");
        let outside = unique_dir("junction-outside");
        let escaped = outside.join("outside.mp4");
        fs::write(&escaped, b"outside").expect("outside fixture should be written");
        let junction = root.join("linked-output");
        let status = Command::new("cmd.exe")
            .args(["/d", "/c", "mklink", "/J"])
            .arg(&junction)
            .arg(&outside)
            .status()
            .expect("Windows junction command should spawn");
        if status.success() {
            let mut escaped_output = request(&root, &binary, &input);
            escaped_output.output_paths =
                vec![junction.join("artifact.mp4").to_string_lossy().to_string()];
            assert_eq!(
                validate_request(&escaped_output).unwrap_err().0,
                "EXECUTOR_PATH_FORBIDDEN"
            );
        }
        let _ = fs::remove_dir_all(root);
        let _ = fs::remove_dir_all(outside);
        unsafe { env::remove_var("FLUJO_EXECUTOR_TOKEN") };
    }

    #[test]
    fn missing_input_and_output_escape_are_rejected() {
        let _lock = ENV_LOCK
            .lock()
            .expect("environment lock should be available");
        unsafe { env::set_var("FLUJO_EXECUTOR_TOKEN", "test-token") };
        let root = unique_dir("paths");
        let media = root.join("media");
        fs::create_dir_all(&media).expect("media directory should be created");
        let binary = media.join("ffmpeg.exe");
        let input = root.join("input.mp4");
        fs::write(&binary, b"test-binary").expect("binary fixture should be written");
        fs::write(&input, b"test-input").expect("input fixture should be written");
        let valid = request(&root, &binary, &input);

        let mut missing = valid.clone();
        missing.input_paths = vec![root.join("missing.mp4").to_string_lossy().to_string()];
        assert_eq!(
            validate_request(&missing).unwrap_err().0,
            "EXECUTOR_INPUT_INVALID"
        );

        let outside = unique_dir("output");
        let mut output = valid;
        output.output_paths = vec![outside.join("artifact.mp4").to_string_lossy().to_string()];
        assert_eq!(
            validate_request(&output).unwrap_err().0,
            "EXECUTOR_PATH_FORBIDDEN"
        );

        let _ = fs::remove_dir_all(root);
        let _ = fs::remove_dir_all(outside);
        unsafe { env::remove_var("FLUJO_EXECUTOR_TOKEN") };
    }

    #[cfg(windows)]
    #[test]
    fn symlink_escape_is_rejected_when_windows_allows_fixture_creation() {
        use std::os::windows::fs::symlink_file;

        let _lock = ENV_LOCK
            .lock()
            .expect("environment lock should be available");
        unsafe { env::set_var("FLUJO_EXECUTOR_TOKEN", "test-token") };
        let root = unique_dir("symlink");
        let media = root.join("media");
        fs::create_dir_all(&media).expect("media directory should be created");
        let binary = media.join("ffprobe.exe");
        let input = root.join("input.mp4");
        fs::write(&binary, b"test-binary").expect("binary fixture should be written");
        fs::write(&input, b"test-input").expect("input fixture should be written");
        let outside = unique_dir("symlink-outside").join("outside.mp4");
        fs::write(&outside, b"outside").expect("outside fixture should be written");
        let link = root.join("linked.mp4");
        if symlink_file(&outside, &link).is_ok() {
            let mut escaped = request(&root, &binary, &input);
            escaped.input_paths = vec![link.to_string_lossy().to_string()];
            assert_eq!(
                validate_request(&escaped).unwrap_err().0,
                "EXECUTOR_PATH_FORBIDDEN"
            );
        }
        let _ = fs::remove_dir_all(root);
        let _ = fs::remove_dir_all(outside.parent().unwrap());
        unsafe { env::remove_var("FLUJO_EXECUTOR_TOKEN") };
    }

    #[test]
    fn output_file_limit_detection_is_bounded() {
        let root = unique_dir("output-limit");
        let output = root.join("artifact.mp4");
        fs::write(&output, vec![b'x'; 16]).expect("output fixture should be written");
        assert!(output_exceeds_limit(
            &[output.to_string_lossy().to_string()],
            8
        ));
        assert!(!output_exceeds_limit(
            &[output.to_string_lossy().to_string()],
            32
        ));
        let _ = fs::remove_dir_all(root);
    }

    #[test]
    fn strict_protocol_rejects_duplicate_keys_and_oversized_lines() {
        let mut seen = HashSet::new();
        let duplicate = process_line(r#"{"protocol_version":1,"protocol_version":1}"#, &mut seen);
        assert!(duplicate.contains("EXECUTOR_DUPLICATE_KEY"));
        let oversized = process_line(&"x".repeat(MAX_REQUEST_LINE_BYTES + 1), &mut seen);
        assert!(oversized.contains("EXECUTOR_REQUEST_TOO_LARGE"));
        let unknown = process_line(r#"{"protocol_version":1,"unexpected":true}"#, &mut seen);
        assert!(unknown.contains("INVALID_REQUEST"));
    }

    #[test]
    fn constant_time_auth_and_identifier_policy_are_strict() {
        assert!(constant_time_equal(b"token", b"token"));
        assert!(!constant_time_equal(b"token", b"tokens"));
        assert!(valid_identifier("operation_01:part"));
        assert!(!valid_identifier("../escape"));
        assert!(!valid_identifier(""));
        assert!(!valid_identifier(&"x".repeat(MAX_IDENTIFIER_BYTES + 1)));
    }

    #[test]
    fn request_validation_rejects_invalid_limits_and_protocol() {
        let _lock = ENV_LOCK
            .lock()
            .expect("environment lock should be available");
        unsafe { env::set_var("FLUJO_EXECUTOR_TOKEN", "test-token") };
        let root = unique_dir("limits");
        let media = root.join("media");
        fs::create_dir_all(&media).expect("media directory should be created");
        let binary = media.join("ffprobe.exe");
        let input = root.join("input.mp4");
        fs::write(&binary, b"test-binary").expect("binary fixture should be written");
        fs::write(&input, b"test-input").expect("input fixture should be written");
        let valid = request(&root, &binary, &input);

        let mut operation = valid.clone();
        operation.operation = "shell".to_string();
        assert_eq!(
            validate_request(&operation).unwrap_err().0,
            "EXECUTOR_OPERATION_FORBIDDEN"
        );

        let mut protocol = valid.clone();
        protocol.protocol_version = PROTOCOL_VERSION + 1;
        assert_eq!(
            validate_request(&protocol).unwrap_err().0,
            "EXECUTOR_PROTOCOL_UNSUPPORTED"
        );
        let mut timeout = valid.clone();
        timeout.timeout_ms = 0;
        assert_eq!(
            validate_request(&timeout).unwrap_err().0,
            "EXECUTOR_TIMEOUT_INVALID"
        );
        let mut output = valid.clone();
        output.max_output_bytes = MAX_OUTPUT_BYTES + 1;
        assert_eq!(
            validate_request(&output).unwrap_err().0,
            "EXECUTOR_OUTPUT_LIMIT_INVALID"
        );
        let mut memory = valid.clone();
        memory.max_memory_bytes = Some(MIN_MEMORY_BYTES - 1);
        assert_eq!(
            validate_request(&memory).unwrap_err().0,
            "EXECUTOR_MEMORY_LIMIT_INVALID"
        );
        let mut cpu = valid.clone();
        cpu.max_cpu_time_ms = Some(cpu.timeout_ms + 1);
        assert_eq!(
            validate_request(&cpu).unwrap_err().0,
            "EXECUTOR_CPU_LIMIT_INVALID"
        );
        let mut network = valid.clone();
        network.args = vec!["https://unapproved.example/media.mp4".to_string()];
        assert_eq!(
            validate_request(&network).unwrap_err().0,
            "EXECUTOR_ARGUMENTS_INVALID"
        );
        let mut identity = valid.clone();
        identity.trace_id = Some("../forbidden".to_string());
        assert_eq!(
            validate_request(&identity).unwrap_err().0,
            "INVALID_REQUEST_IDENTITY"
        );

        let _ = fs::remove_dir_all(root);
        unsafe { env::remove_var("FLUJO_EXECUTOR_TOKEN") };
    }
}

fn main() -> io::Result<()> {
    let mut seen_operation_ids = HashSet::new();
    let stdin = io::stdin();
    let mut reader = stdin.lock();
    loop {
        let mut bytes = Vec::with_capacity(MAX_REQUEST_LINE_BYTES + 1);
        let read = reader
            .by_ref()
            .take((MAX_REQUEST_LINE_BYTES + 1) as u64)
            .read_until(b'\n', &mut bytes)?;
        if read == 0 {
            break;
        }
        if !bytes.ends_with(b"\n") {
            let mut discarded = [0u8; 8192];
            loop {
                let count = reader.read(&mut discarded)?;
                if count == 0 || discarded[..count].contains(&b'\n') {
                    break;
                }
            }
            println!("{{\"protocol_version\":1,\"status\":\"blocked\",\"error_code\":\"EXECUTOR_REQUEST_TOO_LARGE\",\"message\":\"The executor request exceeds the protocol size limit.\"}}");
            io::stdout().flush()?;
            continue;
        }
        let line = String::from_utf8_lossy(&bytes);
        if line.trim().is_empty() {
            continue;
        }
        let result = process_line(line.trim(), &mut seen_operation_ids);
        println!("{result}");
        io::stdout().flush()?;
    }
    Ok(())
}
