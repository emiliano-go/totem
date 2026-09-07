use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use std::fmt;

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub enum Entry {
    Str(String),
    Int(i64),
    Float(f64),
    Bool(bool),
    Array(Vec<Entry>),
    Table(HashMap<String, Entry>),
}

impl fmt::Display for Entry {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Entry::Str(s) => write!(f, "Str(\"{}\")", s),
            Entry::Int(i) => write!(f, "Int({})", i),
            Entry::Float(fl) => write!(f, "Float({})", fl),
            Entry::Bool(b) => write!(f, "Bool({})", b),
            Entry::Array(arr) => {
                write!(f, "Array([")?;
                for (i, item) in arr.iter().enumerate() {
                    if i > 0 {
                        write!(f, ", ")?;
                    }
                    write!(f, "{}", item)?;
                }
                write!(f, "])")
            }
            Entry::Table(table) => {
                write!(f, "Table({{")?;
                for (i, (k, v)) in table.iter().enumerate() {
                    if i > 0 {
                        write!(f, ", ")?;
                    }
                    write!(f, "{}: {}", k, v)?;
                }
                write!(f, "}})")
            }
        }
    }
}
