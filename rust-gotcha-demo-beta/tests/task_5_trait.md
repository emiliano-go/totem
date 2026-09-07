# Task 5: Trait Implementation

Implement the `Display` trait for `ConfigStore` in `src/store.rs`.

## Requirements

Implement `std::fmt::Display` for `ConfigStore`. The output should be a human-readable format:

```
ConfigStore {
  key1: Str("value"),
  key2: Int(42),
  key3: Float(3.14),
  key4: Bool(true),
  key5: Array([Str("a"), Int(1)]),
  key6: Table({name: Str("test")})
}
```

The format should:
- Show `ConfigStore {` on the first line
- Each entry indented with 2 spaces
- Keys shown as `key: Variant(args)`
- Arrays shown as `[elem1, elem2]`
- Tables shown as `{key1: val1, key2: val2}`
- Closing `}` on the last line

You'll need to implement `Display` for `Entry` as well to make this work.

## Verification

Run `cargo check` after implementation.
