package main

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func writeTemp(t *testing.T, contents string) string {
	t.Helper()
	dir := t.TempDir()
	p := filepath.Join(dir, "pyproject.toml")
	if err := os.WriteFile(p, []byte(contents), 0o644); err != nil {
		t.Fatal(err)
	}
	return p
}

func TestRunSetThenGet(t *testing.T) {
	p := writeTemp(t, sample)

	if err := run([]string{"set-python-version", p, "3.13"}); err != nil {
		t.Fatalf("set: %v", err)
	}
	data, err := os.ReadFile(p)
	if err != nil {
		t.Fatalf("read: %v", err)
	}
	if !strings.Contains(string(data), ">=3.13") {
		t.Errorf("file not edited:\n%s", data)
	}
}

func TestRunGetDoesNotWrite(t *testing.T) {
	p := writeTemp(t, sample)
	before, err := os.ReadFile(p)
	if err != nil {
		t.Fatalf("read: %v", err)
	}
	if err := run([]string{"get-python-version", p}); err != nil {
		t.Fatalf("get: %v", err)
	}
	after, err := os.ReadFile(p)
	if err != nil {
		t.Fatalf("read: %v", err)
	}
	if string(before) != string(after) {
		t.Errorf("get-* must not modify the file:\nbefore:\n%s\nafter:\n%s", before, after)
	}
}

func TestRunUnknownCommand(t *testing.T) {
	p := writeTemp(t, sample)
	if err := run([]string{"bogus", p}); err == nil {
		t.Error("expected error for unknown command")
	}
}

func TestRunRequiresValue(t *testing.T) {
	p := writeTemp(t, sample)
	if err := run([]string{"set-python-version", p}); err == nil {
		t.Error("expected error when value is missing")
	}
}
