package main

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestRenderedPackageMatchesTheRuntime(t *testing.T) {
	for _, tc := range []struct {
		name, project, pkg string
	}{
		{"module3", "module3", "module3"},
		{"m2", "m2", "m2"},
		{"Module3", "module3", "module3"},
		{"myModule", "mymodule", "mymodule"},
		{"MyModule", "mymodule", "mymodule"},
		{"my-module", "my-module", "my_module"},
		{"my_module", "my_module", "my_module"},
		{"ep-target-shared", "ep-target-shared", "ep_target_shared"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			out := t.TempDir()
			if err := run([]string{tc.name, filepath.Join("..", "..", "templates", "default"), out}); err != nil {
				t.Fatal(err)
			}
			if _, err := os.Stat(filepath.Join(out, "src", tc.pkg, "__init__.py")); err != nil {
				t.Errorf("package directory: %v", err)
			}
			pyproject, err := os.ReadFile(filepath.Join(out, "pyproject.toml"))
			if err != nil {
				t.Fatal(err)
			}
			if want := `name = "` + tc.project + `"`; !strings.Contains(string(pyproject), want) {
				t.Errorf("pyproject.toml does not declare %s:\n%s", want, pyproject)
			}
		})
	}
}
