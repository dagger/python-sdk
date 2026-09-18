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

func TestRunEditScope(t *testing.T) {
	p := writeTemp(t, sample)
	err := run([]string{
		"edit-scope", p,
		"--member", "sdk", "--member", "clients/core", "--member", "clients/linter",
		"--source", "dagger-io", "--source", "dagger-clients-core", "--source", "dagger-clients-linter",
		"--dependency", "dagger-clients-core", "--dependency", "dagger-clients-linter",
		"--global-client", "true",
	})
	if err != nil {
		t.Fatalf("edit-scope: %v", err)
	}
	data, err := os.ReadFile(p)
	if err != nil {
		t.Fatalf("read: %v", err)
	}
	for _, want := range []string{
		`members = ["sdk", "clients/core", "clients/linter"]`,
		"dagger-io = { workspace = true }",
		"dagger-clients-linter = { workspace = true }",
		`dependencies = ["dagger-io", "dagger-clients-core", "dagger-clients-linter"]`,
		"global-client = true",
	} {
		if !strings.Contains(string(data), want) {
			t.Errorf("missing %q in:\n%s", want, data)
		}
	}
	if strings.Contains(string(data), `path = "sdk"`) {
		t.Errorf("the vendored source survived:\n%s", data)
	}
}

func TestRunEditScopeRejectsAnUnknownFlag(t *testing.T) {
	p := writeTemp(t, sample)
	if err := run([]string{"edit-scope", p, "--bogus", "x"}); err == nil {
		t.Error("expected an error for an unknown flag")
	}
}

func TestRunSetGlobalClient(t *testing.T) {
	p := writeTemp(t, sample)
	if err := run([]string{"set-global-client", p, "true"}); err != nil {
		t.Fatalf("set: %v", err)
	}
	data, _ := os.ReadFile(p)
	if !strings.Contains(string(data), "global-client = true") {
		t.Errorf("flag not written:\n%s", data)
	}
	if err := run([]string{"set-global-client", p, "false"}); err != nil {
		t.Fatalf("clear: %v", err)
	}
	data, _ = os.ReadFile(p)
	if strings.Contains(string(data), "global-client") || strings.Contains(string(data), "[tool.dagger]") {
		t.Errorf("clearing the flag left it or its table behind:\n%s", data)
	}
}
