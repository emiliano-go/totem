# Task 4: Numeric Helpers

Add numeric helper methods to `ConfigStore` in `src/store.rs`.

## Requirements

Add these methods:

```rust
pub fn get_int(&self, key: &str) -> Option<i64>
pub fn get_float(&self, key: &str) -> Option<f64>
pub fn set_int(&mut self, key: &str, value: i64)
pub fn set_float(&mut self, key: &str, value: f64)
```

- `get_int` returns `Some(i64)` only if the entry is `Entry::Int`
- `get_float` returns `Some(f64)` only if the entry is `Entry::Float`
- `set_int` inserts `Entry::Int(value)` at the given key
- `set_float` inserts `Entry::Float(value)` at the given key

## Verification

Run `cargo check` after implementation.
