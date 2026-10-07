package main

import (
	"io"
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

	err = run([]string{
		"edit-scope", p,
		"--member", "sdk", "--member", "clients/core", "--stale-member", "clients/linter",
		"--source", "dagger-io", "--source", "dagger-clients-core",
		"--dependency", "dagger-clients-core",
	})
	if err != nil {
		t.Fatalf("edit-scope: %v", err)
	}
	if data, _ = os.ReadFile(p); strings.Contains(string(data), "linter") {
		t.Errorf("the stale member survived:\n%s", data)
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

// set-global-client owns one key, not the file: set then cleared, the file
// comes back byte for byte, with its table order, quoting and inline sources.
func TestRunSetGlobalClientRoundTripsTheFile(t *testing.T) {
	for name, src := range map[string]string{
		"no dagger table": `[project]
name = 'config'   # single quotes
dependencies = [
    "dagger-io",
    "dagger-clients-core",
]

[tool.uv.sources]
dagger-io = { workspace = true }
dagger-clients-core = { workspace = true }

[tool.uv.workspace]
members = ["sdk", "clients/core"]

[build-system]
requires = ["uv_build>=0.8.4,<0.12.0"]
build-backend = "uv_build"
`,
		"a dagger table in the middle": `[project]
name = "config"
requires-python = ">=3.12"

[tool.dagger]
use-uv = false  # a comment of the user's
base-image = "python:3.12-slim"

[tool.uv.sources]
dagger-io = { workspace = true }
`,
	} {
		t.Run(name, func(t *testing.T) {
			p := writeTemp(t, src)
			if err := run([]string{"set-global-client", p, "true"}); err != nil {
				t.Fatalf("set: %v", err)
			}
			data, _ := os.ReadFile(p)
			if !strings.Contains(string(data), "global-client = true") {
				t.Fatalf("flag not written:\n%s", data)
			}
			if err := run([]string{"set-global-client", p, "false"}); err != nil {
				t.Fatalf("clear: %v", err)
			}
			if data, _ = os.ReadFile(p); string(data) != src {
				t.Errorf("clearing the flag did not give the file back:\ngot:\n%s\nwant:\n%s", data, src)
			}
		})
	}
}

func TestRunSetGlobalClientRejectsAnotherValue(t *testing.T) {
	p := writeTemp(t, sample)
	if err := run([]string{"set-global-client", p, "yes"}); err == nil {
		t.Error("expected an error for a value that is not true or false")
	}
}

func printed(t *testing.T, args ...string) string {
	t.Helper()
	r, w, err := os.Pipe()
	if err != nil {
		t.Fatal(err)
	}
	stdout := os.Stdout
	os.Stdout = w
	runErr := run(args)
	os.Stdout = stdout
	w.Close()
	out, err := io.ReadAll(r)
	if err != nil {
		t.Fatal(err)
	}
	if runErr != nil {
		t.Fatalf("%v: %v", args, runErr)
	}
	return string(out)
}

func TestRunGetGenerated(t *testing.T) {
	for name, tc := range map[string]struct {
		src  string
		want string
	}{
		"marked":     {"[project]\nname = \"dagger-clients-linter\"\n\n[tool.dagger]\ngenerated = \"client\"\n", "client"},
		"not marked": {sample, ""},
		"inside a multiline string": {`[project]
name = "notes"
description = """
[tool.dagger]
generated = "client"
"""
`, ""},
		"unparsable": {"[tool.dagger]\ngenerated = \"client\"\n[tool.dagger]\n", ""},
	} {
		t.Run(name, func(t *testing.T) {
			if got := printed(t, "get-generated", writeTemp(t, tc.src)); got != tc.want {
				t.Errorf("got %q, want %q", got, tc.want)
			}
		})
	}
}

func TestRunGetGlobalClientReadsTheFlagAsTOML(t *testing.T) {
	for name, tc := range map[string]struct {
		src  string
		want string
	}{
		"set":   {"[tool.dagger]\nglobal-client = true\n", "true"},
		"unset": {sample, ""},
		"inside a multiline string": {`[project]
name = "notes"
description = """
[tool.dagger]
global-client = true
"""
`, ""},
	} {
		t.Run(name, func(t *testing.T) {
			if got := printed(t, "get-global-client", writeTemp(t, tc.src)); got != tc.want {
				t.Errorf("got %q, want %q", got, tc.want)
			}
		})
	}
}

func TestRunRemoveTable(t *testing.T) {
	src := "name = 'demo'\n\n[entrypoint]\nkind = \"module\"\nsource = \"../runtime\"\n\n[runtime]\nsource = \"python\"\n"
	p := writeTemp(t, src)
	if err := run([]string{"remove-table", p, "entrypoint"}); err != nil {
		t.Fatalf("remove-table: %v", err)
	}
	want := "name = 'demo'\n\n[runtime]\nsource = \"python\"\n"
	if data, _ := os.ReadFile(p); string(data) != want {
		t.Errorf("got:\n%s\nwant:\n%s", data, want)
	}
	if err := run([]string{"remove-table", p}); err == nil {
		t.Error("expected an error when the table is missing")
	}
}
