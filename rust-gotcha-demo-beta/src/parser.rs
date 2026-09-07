use crate::Entry;
use std::collections::HashMap;

fn toml_value_to_entry(val: toml::Value) -> Entry {
    match val {
        toml::Value::String(s) => Entry::Str(s),
        toml::Value::Integer(i) => Entry::Int(i),
        toml::Value::Float(f) => Entry::Float(f),
        toml::Value::Boolean(b) => Entry::Bool(b),
        toml::Value::Array(arr) => Entry::Array(arr.into_iter().map(toml_value_to_entry).collect()),
        toml::Value::Table(table) => {
            Entry::Table(table.into_iter().map(|(k, v)| (k, toml_value_to_entry(v))).collect())
        }
        toml::Value::Datetime(dt) => Entry::Str(dt.to_string()),
    }
}

fn json_value_to_entry(val: serde_json::Value) -> Result<Entry, String> {
    match val {
        serde_json::Value::String(s) => Ok(Entry::Str(s)),
        serde_json::Value::Number(n) => {
            if let Some(i) = n.as_i64() {
                Ok(Entry::Int(i))
            } else if let Some(f) = n.as_f64() {
                Ok(Entry::Float(f))
            } else {
                Err("number out of range".to_string())
            }
        }
        serde_json::Value::Bool(b) => Ok(Entry::Bool(b)),
        serde_json::Value::Array(arr) => {
            let mut entries = Vec::new();
            for item in arr {
                entries.push(json_value_to_entry(item)?);
            }
            Ok(Entry::Array(entries))
        }
        serde_json::Value::Object(obj) => {
            let mut map = HashMap::new();
            for (k, v) in obj {
                map.insert(k, json_value_to_entry(v)?);
            }
            Ok(Entry::Table(map))
        }
        serde_json::Value::Null => Err("null not supported".to_string()),
    }
}

pub fn parse_toml(input: &str) -> Result<HashMap<String, Entry>, String> {
    let val: toml::Value = toml::from_str(input).map_err(|e| e.to_string())?;
    match val {
        toml::Value::Table(table) => Ok(table
            .into_iter()
            .map(|(k, v)| (k, toml_value_to_entry(v)))
            .collect()),
        _ => Err("top level must be a table".to_string()),
    }
}

pub fn parse_json(input: &str) -> Result<HashMap<String, Entry>, String> {
    let val: serde_json::Value = serde_json::from_str(input).map_err(|e| e.to_string())?;
    match val {
        serde_json::Value::Object(obj) => {
            let mut map = HashMap::new();
            for (k, v) in obj {
                map.insert(k, json_value_to_entry(v)?);
            }
            Ok(map)
        }
        _ => Err("top level must be an object".to_string()),
    }
}
