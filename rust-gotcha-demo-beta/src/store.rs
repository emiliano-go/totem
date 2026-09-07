use crate::Entry;
use std::collections::HashMap;
use std::fmt;

#[derive(Debug, Default)]
pub struct ConfigStore {
    entries: HashMap<String, Entry>,
}

impl ConfigStore {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn entry(&mut self, key: &str) -> &mut Entry {
        self.entries
            .entry(key.to_string())
            .or_insert_with(|| Entry::Str(String::new()))
    }

    pub fn get_int(&self, key: &str) -> Option<i64> {
        match self.entries.get(key)? {
            Entry::Int(i) => Some(*i),
            _ => None,
        }
    }

    pub fn get_float(&self, key: &str) -> Option<f64> {
        match self.entries.get(key)? {
            Entry::Float(f) => Some(*f),
            _ => None,
        }
    }

    pub fn set_int(&mut self, key: &str, value: i64) {
        self.entries.insert(key.to_string(), Entry::Int(value));
    }

    pub fn set_float(&mut self, key: &str, value: f64) {
        self.entries.insert(key.to_string(), Entry::Float(value));
    }

    // TODO: task 5 - trait impl
}

impl fmt::Display for ConfigStore {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        writeln!(f, "ConfigStore {{")?;
        for (key, value) in &self.entries {
            writeln!(f, "  {}: {}", key, value)?;
        }
        write!(f, "}}")
    }
}
