//! Path utility helpers for generating unique filenames

use std::path::{Path, PathBuf};

/// Return a path that avoids collisions in the target directory.
///
/// # Arguments
/// * `directory` - Directory to place the file in
/// * `filename` - Original filename to base uniqueness on
/// * `pattern` - Pattern for uniqueness, uses {stem}, {ext}, {i} placeholders
/// * `use_original` - If true, return the original filename when available
///
/// # Example
/// ```
/// use justicepdf::path_utils::ensure_unique_path;
/// use std::path::Path;
///
/// let path = ensure_unique_path(
///     Path::new("/tmp"),
///     "test.pdf",
///     "{stem} ({i}){ext}",
///     true,
/// );
/// ```
pub fn ensure_unique_path(
    directory: &Path,
    filename: &str,
    pattern: &str,
    use_original: bool,
) -> PathBuf {
    let base = Path::new(filename);
    let stem = base.file_stem().unwrap_or_default().to_string_lossy();
    let ext = base
        .extension()
        .map(|e| format!(".{}", e.to_string_lossy()))
        .unwrap_or_default();

    if use_original {
        let candidate = directory.join(filename);
        if !candidate.exists() {
            return candidate;
        }
    }

    let mut i = 1u32;
    loop {
        let new_name = pattern
            .replace("{stem}", &stem)
            .replace("{ext}", &ext)
            .replace("{i}", &i.to_string());

        let candidate = directory.join(&new_name);
        if !candidate.exists() {
            return candidate;
        }
        i += 1;

        // Safety limit to prevent infinite loop
        if i > 10000 {
            // Return a UUID-based name as fallback
            let uuid = uuid::Uuid::new_v4();
            return directory.join(format!("{}_{}{}", stem, uuid, ext));
        }
    }
}

/// Generate a unique path with copy suffix
pub fn ensure_unique_copy_path(directory: &Path, filename: &str) -> PathBuf {
    ensure_unique_path(directory, filename, "{stem}_copy_{i}{ext}", false)
}

/// Generate a unique path with standard numbering
pub fn ensure_unique_numbered_path(directory: &Path, filename: &str) -> PathBuf {
    ensure_unique_path(directory, filename, "{stem} ({i}){ext}", true)
}

/// Generate a unique path for extracted pages
pub fn ensure_unique_pages_path(directory: &Path, filename: &str) -> PathBuf {
    ensure_unique_path(directory, filename, "{stem}_pages_{i}{ext}", false)
}

/// Normalize a path for comparison (resolve and canonicalize)
pub fn normalize_path(path: &Path) -> PathBuf {
    path.canonicalize().unwrap_or_else(|_| path.to_path_buf())
}

/// Check if two paths refer to the same file
pub fn paths_equal(path1: &Path, path2: &Path) -> bool {
    normalize_path(path1) == normalize_path(path2)
}

#[cfg(test)]
mod tests {
    use super::*;
    use tempfile::tempdir;

    #[test]
    fn test_ensure_unique_path_no_conflict() {
        let dir = tempdir().unwrap();
        let path = ensure_unique_path(dir.path(), "test.pdf", "{stem} ({i}){ext}", true);
        assert_eq!(path, dir.path().join("test.pdf"));
    }

    #[test]
    fn test_ensure_unique_path_with_conflict() {
        let dir = tempdir().unwrap();

        // Create existing file
        std::fs::write(dir.path().join("test.pdf"), "").unwrap();

        let path = ensure_unique_path(dir.path(), "test.pdf", "{stem} ({i}){ext}", true);
        assert_eq!(path, dir.path().join("test (1).pdf"));
    }

    #[test]
    fn test_ensure_unique_copy_path() {
        let dir = tempdir().unwrap();
        let path = ensure_unique_copy_path(dir.path(), "test.pdf");
        assert_eq!(path, dir.path().join("test_copy_1.pdf"));
    }

    #[test]
    fn test_ensure_unique_pages_path() {
        let dir = tempdir().unwrap();
        let path = ensure_unique_pages_path(dir.path(), "document.pdf");
        assert_eq!(path, dir.path().join("document_pages_1.pdf"));
    }
}
