package main

import (
	"os"
	"path/filepath"
	"testing"
)

func TestGeneratedKindReadsTheMarkerAsTOML(t *testing.T) {
	for name, tc := range map[string]struct {
		src  string
		want string
	}{
		"client":       {"[project]\nname = \"x\"\n\n[tool.dagger]\ngenerated = \"client\"\n", "client"},
		"core":         {"[tool.dagger]\ngenerated = 'core'\n", "core"},
		"runtime":      {"[tool.dagger]  # the SDK files\ngenerated = \"runtime\"\n", "runtime"},
		"dotted":       {"tool.dagger.generated = \"client\"\n", "client"},
		"none":         {"[project]\nname = \"x\"\n", ""},
		"unknown kind": {"[tool.dagger]\ngenerated = \"hand-written\"\n", ""},
		"not a string": {"[tool.dagger]\ngenerated = true\n", ""},
		"unparsable":   {"[tool.dagger]\ngenerated = \"client\"\n[tool.dagger]\n", ""},
		"inside a multiline string": {`[project]
name = "handmade"
description = """
[tool.dagger]
generated = "client"
"""
`, ""},
	} {
		t.Run(name, func(t *testing.T) {
			if got := generatedKind([]byte(tc.src)); got != tc.want {
				t.Errorf("got %q, want %q", got, tc.want)
			}
		})
	}
}

func TestRunGetMemberKinds(t *testing.T) {
	scope := t.TempDir()
	write := func(rel, contents string) {
		p := filepath.Join(scope, rel)
		if err := os.MkdirAll(filepath.Dir(p), 0o755); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(p, []byte(contents), 0o644); err != nil {
			t.Fatal(err)
		}
	}
	write("sdk/pyproject.toml", "[tool.dagger]\ngenerated = \"runtime\"\n")
	write("clients/core/pyproject.toml", "[tool.dagger]\ngenerated = \"core\"\n")
	write("clients/linter/pyproject.toml", "[tool.dagger]\ngenerated = \"client\"\n")
	write("clients/handmade/pyproject.toml", "description = \"\"\"\n[tool.dagger]\ngenerated = \"client\"\n\"\"\"\n")
	write("clients/odd/pyproject.toml", "[tool.dagger]\ngenerated = \"hand-written\"\n")
	write("clients/notes/README.md", "no project\n")

	got, err := memberKinds(scope)
	if err != nil {
		t.Fatal(err)
	}
	want := "clients/core core\nclients/linter client\nsdk runtime\n"
	if got != want {
		t.Errorf("got:\n%s\nwant:\n%s", got, want)
	}

	if got, err := memberKinds(t.TempDir()); err != nil || got != "" {
		t.Errorf("an empty scope: got %q, %v", got, err)
	}
}
