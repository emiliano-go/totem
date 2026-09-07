# Task 3: Entry Method

Add the `entry` method to `ConfigStore` in `src/store.rs`.

## Requirements

```rust
pub fn entry(&mut self, key: &str) -> &mut Entry
```

This method should:
1. If the key already exists, return a mutable reference to its value
2. If the key doesn't exist, insert `Entry::Str(String::new())` and return a mutable reference to the new entry

This is similar to `HashMap::entry` but works with `Entry` specifically.

## Verification

Run `cargo check` after implementation.
