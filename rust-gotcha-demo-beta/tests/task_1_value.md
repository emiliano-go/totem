# Task 1: Value Type

Implement the `Entry` enum in `src/entry.rs`.

## Requirements

The enum should have these variants:

- `Str(String)` — a string value
- `Int(i64)` — an integer
- `Float(f64)` — a floating point number
- `Bool(bool)` — a boolean
- `Array(Vec<Entry>)` — a list of entries (recursive)
- `Table(HashMap<String, Entry>)` — a nested table (recursive)

Add the necessary imports. The derive macros are already there.

## Verification

After implementation, run `cargo check`. It should compile with no errors.
