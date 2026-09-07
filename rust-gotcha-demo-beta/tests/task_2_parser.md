# Task 2: Parsers

Implement `parse_toml` and `parse_json` in `src/parser.rs`.

## Requirements

Both functions take a `&str` and return `Result<HashMap<String, Entry>, String>`.

### parse_toml

Use the `toml` crate to parse the input into a `toml::Value`, then convert it into `HashMap<String, Entry>`.

The conversion should handle:
- `toml::Value::String` → `Entry::Str`
- `toml::Value::Integer` → `Entry::Int`
- `toml::Value::Float` → `Entry::Float`
- `toml::Value::Boolean` → `Entry::Bool`
- `toml::Value::Array` → `Entry::Array` (recursively convert each element)
- `toml::Value::Table` → `Entry::Table` (recursively convert each entry)

Return `Err(e.to_string())` on parse failure.

### parse_json

Use `serde_json` to parse the input into a `serde_json::Value`, then convert it into `HashMap<String, Entry>`.

The conversion should handle:
- `serde_json::Value::String` → `Entry::Str`
- `serde_json::Value::Number` → `Entry::Int` if it's an integer, `Entry::Float` otherwise
- `serde_json::Value::Bool` → `Entry::Bool`
- `serde_json::Value::Array` → `Entry::Array` (recursively convert each element)
- `serde_json::Value::Object` → `Entry::Table` (recursively convert each entry)
- `serde_json::Value::Null` → return error "null not supported"

Return `Err(e.to_string())` on parse failure.

## Verification

Run `cargo check` after implementation.
