package main

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

const appProject = `[project]
name = "app"
dependencies = ["dagger-io", "greet"]

[tool.uv.workspace]
members = ["sdk"]

[tool.uv.sources]
dagger-io = { workspace = true }
greet = { path = "../../libs/greet", editable = true }
`

func writeTree(t *testing.T, files map[string]string) string {
	t.Helper()
	tree := t.TempDir()
	for rel, contents := range files {
		path := filepath.Join(tree, rel)
		if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(path, []byte(contents), 0o644); err != nil {
			t.Fatal(err)
		}
	}
	return tree
}

func patterns(resources []resource) []string {
	out := make([]string, len(resources))
	for i, r := range resources {
		out[i] = r.Pattern
	}
	return out
}

func TestDeriveResourcesPrintsJSON(t *testing.T) {
	tree := writeTree(t, map[string]string{
		"services/app/pyproject.toml":     appProject,
		"services/app/sdk/pyproject.toml": "[project]\nname = \"dagger-io\"\n",
		"libs/greet/pyproject.toml":       "[project]\nname = \"greet\"\n\n[tool.uv.sources]\ntext = { path = \"../text\" }\n",
		"libs/text/pyproject.toml":        "[project]\nname = \"text\"\n",
	})
	got := printed(t, "derive-resources", tree, "services/app")
	want := `[{"pattern":"../../libs/greet","rooted":"libs/greet"},{"pattern":"../../libs/text","rooted":"libs/text"}]`
	if got != want {
		t.Errorf("got %s, want %s", got, want)
	}
	if got := printed(t, "derive-resources", writeTree(t, map[string]string{"app/pyproject.toml": "[project]\n"}), "app"); got != "[]" {
		t.Errorf("nothing outside: got %s, want []", got)
	}
}

func TestDeriveResourcesKeepsAnyPathCharacter(t *testing.T) {
	odd := "../odd\tname\nhere"
	tree := writeTree(t, map[string]string{
		"app/pyproject.toml": "[tool.uv.sources]\nodd = { path = \"../odd\\tname\\nhere\" }\n",
	})
	var got []resource
	if err := json.Unmarshal([]byte(printed(t, "derive-resources", tree, "app")), &got); err != nil {
		t.Fatal(err)
	}
	if len(got) != 1 || got[0].Pattern != odd || got[0].Rooted != "odd\tname\nhere" {
		t.Fatalf("got %q", got)
	}
}

func TestDeriveResources(t *testing.T) {
	for name, tc := range map[string]struct {
		files map[string]string
		want  []string
	}{
		"no sources": {files: map[string]string{
			"app/pyproject.toml": "[project]\nname = \"app\"\n",
		}},
		"no pyproject.toml": {files: map[string]string{
			"app/uv.lock": "version = 1\n",
		}},
		"a path source inside the module": {files: map[string]string{
			"app/pyproject.toml":          "[tool.uv.sources]\na = { path = \"vendor/a\" }\n",
			"app/vendor/a/pyproject.toml": "[tool.uv.sources]\nb = { path = \"../b\" }\n",
		}},
		"a path source inside reading one outside": {
			files: map[string]string{
				"app/pyproject.toml":          "[tool.uv.sources]\na = { path = \"vendor/a\" }\n",
				"app/vendor/a/pyproject.toml": "[tool.uv.sources]\nshared = { path = \"../../../shared\" }\n",
				"shared/pyproject.toml":       "[project]\nname = \"shared\"\n",
			},
			want: []string{"../shared"},
		},
		"sources in name order, then theirs": {
			files: map[string]string{
				"app/pyproject.toml":   "[tool.uv.sources]\nzeta = { path = \"../zeta\" }\nalpha = { path = \"../alpha/\" }\n",
				"alpha/pyproject.toml": "[tool.uv.sources]\ndeep = { path = \"../deep\" }\n",
				"zeta/pyproject.toml":  "[project]\nname = \"zeta\"\n",
				"deep/pyproject.toml":  "[project]\nname = \"deep\"\n",
			},
			want: []string{"../alpha", "../zeta", "../deep"},
		},
		"one directory named twice": {
			files: map[string]string{
				"app/pyproject.toml":    "[tool.uv.sources]\na = { path = \"../shared\" }\nb = { path = \"../shared/./\" }\n",
				"shared/pyproject.toml": "[tool.uv.sources]\nback = { path = \"../app\" }\n",
			},
			want: []string{"../shared"},
		},
		"a path source by marker": {
			files: map[string]string{
				"app/pyproject.toml": `[tool.uv.sources]
greet = [
  { path = "../greet", marker = "sys_platform == 'linux'" },
  { index = "pypi", marker = "sys_platform != 'linux'" },
]
`,
				"greet/pyproject.toml": "[project]\nname = \"greet\"\n",
			},
			want: []string{"../greet"},
		},
		"a wheel": {
			files: map[string]string{
				"app/pyproject.toml": "[tool.uv.sources]\nwheel = { path = \"../dist/wheel-1.0-py3-none-any.whl\" }\n",
			},
			want: []string{"../dist/wheel-1.0-py3-none-any.whl"},
		},
		"an excluded workspace member": {
			files: map[string]string{
				"app/pyproject.toml":     "[tool.uv.workspace]\nmembers = [\"../libs/*\"]\nexclude = [\"../libs/b\"]\n",
				"libs/a/pyproject.toml":  "[project]\nname = \"a\"\n",
				"libs/b/pyproject.toml":  "[tool.uv.sources]\nc = { path = \"../../other/c\" }\n",
				"other/c/pyproject.toml": "[project]\nname = \"c\"\n",
			},
			want: []string{"../libs/a"},
		},
		"workspace members outside the module": {
			files: map[string]string{
				"app/pyproject.toml":     "[tool.uv.workspace]\nmembers = [\"sdk\", \"../libs/*\"]\n",
				"app/sdk/pyproject.toml": "[project]\nname = \"dagger-io\"\n",
				"libs/b/pyproject.toml":  "[project]\nname = \"b\"\n",
				"libs/a/pyproject.toml":  "[tool.uv.sources]\nc = { path = \"../c\" }\n",
				"libs/c/pyproject.toml":  "[project]\nname = \"c\"\n",
				"libs/none/README.md":    "not a member\n",
			},
			want: []string{"../libs/a", "../libs/b", "../libs/c"},
		},
		"a lock source no pyproject.toml reaches": {
			files: map[string]string{
				"app/pyproject.toml":   "[tool.uv.sources]\ngreet = { path = \"../greet\" }\n",
				"greet/pyproject.toml": "[project]\nname = \"greet\"\n",
				"app/uv.lock": `version = 1

[[package]]
name = "app"
source = { virtual = "." }

[[package]]
name = "dagger-io"
source = { editable = "sdk" }

[[package]]
name = "greet"
source = { directory = "../greet" }

[[package]]
name = "plain"
source = { directory = "../vendored/plain" }

[[package]]
name = "member"
source = { virtual = "../members/member" }

[[package]]
name = "requests"
source = { registry = "https://pypi.org/simple" }
`,
			},
			want: []string{"../greet", "../members/member", "../vendored/plain"},
		},
	} {
		t.Run(name, func(t *testing.T) {
			got, err := deriveResources(writeTree(t, tc.files), "app")
			if err != nil {
				t.Fatal(err)
			}
			if strings.Join(patterns(got), " ") != strings.Join(tc.want, " ") {
				t.Fatalf("got %q, want %q", patterns(got), tc.want)
			}
		})
	}
}

func TestDeriveResourcesRefusals(t *testing.T) {
	for name, tc := range map[string]struct {
		files map[string]string
		err   string
	}{
		"an absolute path source": {
			files: map[string]string{"app/pyproject.toml": "[tool.uv.sources]\ngreet = { path = \"/opt/greet\" }\n"},
			err:   `app/pyproject.toml: [tool.uv.sources] greet path "/opt/greet" is absolute; a module loads only files inside its workspace`,
		},
		"a path source above the workspace": {
			files: map[string]string{"app/pyproject.toml": "[tool.uv.sources]\ngreet = { path = \"../../greet\" }\n"},
			err:   `app/pyproject.toml: [tool.uv.sources] greet path "../../greet" leaves the workspace root`,
		},
		"a path source's own source above the workspace": {
			files: map[string]string{
				"app/pyproject.toml":   "[tool.uv.sources]\ngreet = { path = \"../greet\" }\n",
				"greet/pyproject.toml": "[tool.uv.sources]\ntext = { path = \"../../text\" }\n",
			},
			err: `greet/pyproject.toml: [tool.uv.sources] text path "../../text" leaves the workspace root`,
		},
		"a member above the workspace": {
			files: map[string]string{"app/pyproject.toml": "[tool.uv.workspace]\nmembers = [\"../../*\"]\n"},
			err:   `app/pyproject.toml: [tool.uv.workspace] members "../../*" leaves the workspace root`,
		},
		"a member that does not parse": {
			files: map[string]string{
				"app/pyproject.toml":              "[tool.uv.workspace]\nmembers = [\"tools/*\"]\n",
				"app/tools/unused/pyproject.toml": "[project\n",
			},
			err: "app/tools/unused/pyproject.toml is not valid TOML: toml: expected character ]",
		},
		"a module that does not parse": {
			files: map[string]string{"app/pyproject.toml": "[project\n"},
			err:   "app/pyproject.toml is not valid TOML: toml: expected character ]",
		},
		"a path source with a backslash": {
			files: map[string]string{"app/pyproject.toml": "[tool.uv.sources]\ngreet = { path = '../lib\\greet' }\n"},
			err:   `app/pyproject.toml: [tool.uv.sources] greet path "../lib\\greet" holds a backslash, which Dagger reads as a path separator; rename the directory to load it`,
		},
		"a lock source with a backslash": {
			files: map[string]string{
				"app/pyproject.toml": "[project]\nname = \"app\"\n",
				"app/uv.lock":        "version = 1\n\n[[package]]\nname = \"greet\"\nsource = { editable = '../lib\\greet' }\n",
			},
			err: `app/uv.lock: "../lib\\greet" holds a backslash, which Dagger reads as a path separator; rename the directory to load it`,
		},
		"an absolute lock source": {
			files: map[string]string{
				"app/pyproject.toml": "[project]\nname = \"app\"\n",
				"app/uv.lock":        "version = 1\n\n[[package]]\nname = \"plain\"\nsource = { directory = \"/opt/plain\" }\n",
			},
			err: `app/uv.lock: plain is at "/opt/plain", an absolute path; a module loads only files inside its workspace`,
		},
	} {
		t.Run(name, func(t *testing.T) {
			_, err := deriveResources(writeTree(t, tc.files), "app")
			if err == nil || err.Error() != tc.err {
				t.Fatalf("got %v, want %q", err, tc.err)
			}
		})
	}
}

func TestDeriveResourcesAtTheRoot(t *testing.T) {
	tree := writeTree(t, map[string]string{"pyproject.toml": "[tool.uv.sources]\ngreet = { path = \"../greet\" }\n"})
	if _, err := deriveResources(tree, "."); err == nil || !strings.Contains(err.Error(), "leaves the workspace root") {
		t.Fatalf("a module at the workspace root reached above it: %v", err)
	}
}
