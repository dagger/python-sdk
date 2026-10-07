package main

import (
	"strings"
	"testing"
)

func boolPtr(b bool) *bool { return &b }

const scopeTemplate = `[project]
name = "my-module"
version = "0.1.0"
requires-python = ">=3.14"
dependencies = ["dagger-io", "dagger-clients-core"]

[build-system]
requires = ["uv_build>=0.8.4,<0.12.0"]
build-backend = "uv_build"

[tool.uv.workspace]
members = ["sdk", "clients/core"]

[tool.uv.sources]
dagger-io = { workspace = true }
dagger-clients-core = { workspace = true }
`

func coreOnly() scopeEdit {
	return scopeEdit{
		Members:      []string{"sdk", "clients/core"},
		Sources:      []string{"dagger-io", "dagger-clients-core"},
		Dependencies: []string{"dagger-clients-core"},
	}
}

func withLinter() scopeEdit {
	e := coreOnly()
	e.Members = append(e.Members, "clients/linter")
	e.Sources = append(e.Sources, "dagger-clients-linter")
	e.Dependencies = append(e.Dependencies, "dagger-clients-linter")
	return e
}

func mustEdit(t *testing.T, src string, edit scopeEdit) string {
	t.Helper()
	out, err := editScope([]byte(src), edit)
	if err != nil {
		t.Fatalf("editScope: %v\ninput:\n%s", err, src)
	}
	return string(out)
}

func TestEditScopeKeepsAMatchingFileByteForByte(t *testing.T) {
	if got := mustEdit(t, scopeTemplate, coreOnly()); got != scopeTemplate {
		t.Errorf("a file that already matches was rewritten:\n%s", got)
	}
}

func TestEditScopeAddsAClient(t *testing.T) {
	got := mustEdit(t, scopeTemplate, withLinter())
	want := `[project]
name = "my-module"
version = "0.1.0"
requires-python = ">=3.14"
dependencies = ["dagger-io", "dagger-clients-core", "dagger-clients-linter"]

[build-system]
requires = ["uv_build>=0.8.4,<0.12.0"]
build-backend = "uv_build"

[tool.uv.workspace]
members = ["sdk", "clients/core", "clients/linter"]

[tool.uv.sources]
dagger-io = { workspace = true }
dagger-clients-core = { workspace = true }
dagger-clients-linter = { workspace = true }
`
	if got != want {
		t.Errorf("got:\n%s\nwant:\n%s", got, want)
	}
}

func TestEditScopeRemovesAClientAndKeepsTheUsersEntries(t *testing.T) {
	src := `# the user's comment stays
[project]
name = "my-module"
dependencies = [
    "dagger-io",
    "dagger-clients-core",
    "dagger-clients-old",
    "httpx>=0.27",  # the user's own dependency
]

[tool.uv.workspace]
members = ["sdk", "clients/core", "clients/old", "tools/mine", "clients/tools"]

[tool.uv.sources]
dagger-io = { workspace = true }
dagger-clients-core = { workspace = true }
dagger-clients-old = { workspace = true }
mine = { workspace = true }
tools = { workspace = true }

[tool.mine]
answer = 42
`
	want := `# the user's comment stays
[project]
name = "my-module"
dependencies = [
    "dagger-io",
    "dagger-clients-core",
    "httpx>=0.27",  # the user's own dependency
]

[tool.uv.workspace]
members = ["sdk", "clients/core", "tools/mine", "clients/tools"]

[tool.uv.sources]
dagger-io = { workspace = true }
dagger-clients-core = { workspace = true }
mine = { workspace = true }
tools = { workspace = true }

[tool.mine]
answer = 42
`
	// clients/old carried the generated marker; clients/tools is the user's
	// own member, in the same directory, and stays.
	edit := coreOnly()
	edit.Stale = []string{"clients/old"}
	if got := mustEdit(t, src, edit); got != want {
		t.Errorf("got:\n%s\nwant:\n%s", got, want)
	}
}

// A member under clients/ is removed only when the caller names it stale:
// the marker in its directory is what makes it generation's.
func TestEditScopeKeepsAnUnnamedMemberUnderClients(t *testing.T) {
	src := `[project]
name = "my-module"
dependencies = ["dagger-io", "dagger-clients-core"]

[tool.uv.workspace]
members = ["sdk", "clients/core", "clients/tools", "clients/*"]

[tool.uv.sources]
dagger-io = { workspace = true }
dagger-clients-core = { workspace = true }
`
	if got := mustEdit(t, src, coreOnly()); got != src {
		t.Errorf("a member generation did not make was touched:\n%s", got)
	}
}

func TestEditScopeAppendsToAMultiLineArrayInItsStyle(t *testing.T) {
	src := `[project]
name = "my-module"
dependencies = [
  "dagger-io",
  "dagger-clients-core",
]

[tool.uv.workspace]
members = [
  "sdk",
  "clients/core",
]

[tool.uv.sources]
dagger-io = { workspace = true }
dagger-clients-core = { workspace = true }
`
	want := `[project]
name = "my-module"
dependencies = [
  "dagger-io",
  "dagger-clients-core",
  "dagger-clients-linter",
]

[tool.uv.workspace]
members = [
  "sdk",
  "clients/core",
  "clients/linter",
]

[tool.uv.sources]
dagger-io = { workspace = true }
dagger-clients-core = { workspace = true }
dagger-clients-linter = { workspace = true }
`
	if got := mustEdit(t, src, withLinter()); got != want {
		t.Errorf("got:\n%s\nwant:\n%s", got, want)
	}
}

func TestEditScopeReplacesTheVendoredSourceAndAddsTheTables(t *testing.T) {
	src := `[project]
name = "my-module"
version = "0.1.0"
requires-python = ">=3.14"
dependencies = ["dagger-io"]

[build-system]
requires = ["uv_build>=0.8.4,<0.12.0"]
build-backend = "uv_build"

[tool.uv.sources]
dagger-io = { path = "sdk", editable = true }
`
	want := `[project]
name = "my-module"
version = "0.1.0"
requires-python = ">=3.14"
dependencies = ["dagger-io", "dagger-clients-core"]

[build-system]
requires = ["uv_build>=0.8.4,<0.12.0"]
build-backend = "uv_build"

[tool.uv.sources]
dagger-io = { workspace = true }
dagger-clients-core = { workspace = true }

[tool.uv.workspace]
members = ["sdk", "clients/core"]
`
	if got := mustEdit(t, src, coreOnly()); got != want {
		t.Errorf("got:\n%s\nwant:\n%s", got, want)
	}
}

func TestEditScopeWritesAnEmptyFile(t *testing.T) {
	want := `[tool.uv.workspace]
members = ["sdk", "clients/core", "clients/linter"]

[tool.uv.sources]
dagger-io = { workspace = true }
dagger-clients-core = { workspace = true }
dagger-clients-linter = { workspace = true }
`
	if got := mustEdit(t, "", withLinter()); got != want {
		t.Errorf("got:\n%s\nwant:\n%s", got, want)
	}
}

func TestEditScopeWritesNoDependencyWithoutAProject(t *testing.T) {
	src := `[tool.uv.workspace]
members = ["sdk", "clients/core"]

[tool.uv.sources]
dagger-io = { workspace = true }
dagger-clients-core = { workspace = true }
`
	got := mustEdit(t, src, withLinter())
	if strings.Contains(got, "[project]") || strings.Contains(got, "dependencies") {
		t.Errorf("a project table was invented:\n%s", got)
	}
	if !strings.Contains(got, `"clients/linter"`) || !strings.Contains(got, "dagger-clients-linter = { workspace = true }") {
		t.Errorf("the member or its source is missing:\n%s", got)
	}
}

func TestEditScopeAddsAMissingKeyToAnExistingTable(t *testing.T) {
	src := `[project]
name = "my-module"

[tool.uv.workspace]
# nothing yet

[tool.uv.sources]
`
	want := `[project]
name = "my-module"
dependencies = ["dagger-clients-core"]

[tool.uv.workspace]
# nothing yet
members = ["sdk", "clients/core"]

[tool.uv.sources]
dagger-io = { workspace = true }
dagger-clients-core = { workspace = true }
`
	if got := mustEdit(t, src, coreOnly()); got != want {
		t.Errorf("got:\n%s\nwant:\n%s", got, want)
	}
}

func TestEditScopeGlobalClientFlag(t *testing.T) {
	on := coreOnly()
	on.GlobalClient = boolPtr(true)
	got := mustEdit(t, scopeTemplate, on)
	if !strings.HasSuffix(got, "\n[tool.dagger]\nglobal-client = true\n") {
		t.Errorf("the flag was not appended:\n%s", got)
	}
	if got != mustEdit(t, got, on) {
		t.Errorf("setting the flag twice changed the file")
	}

	indented := `[project]
name = "my-module"
dependencies = ["dagger-clients-core"]

  [tool.dagger]  # indented, with a trailing comment
  use-uv = false

[tool.uv.workspace]
members = ["sdk", "clients/core"]

[tool.uv.sources]
dagger-io = { workspace = true }
dagger-clients-core = { workspace = true }
`
	got = mustEdit(t, indented, on)
	if !strings.Contains(got, "  use-uv = false\n  global-client = true\n") {
		t.Errorf("the flag did not join the existing table:\n%s", got)
	}

	off := coreOnly()
	off.GlobalClient = boolPtr(false)
	if got = mustEdit(t, got, off); got != indented {
		t.Errorf("clearing the flag did not restore the file:\n%s", got)
	}
	// Clearing prunes a table the flag alone kept.
	if got = mustEdit(t, mustEdit(t, scopeTemplate, on), off); got != scopeTemplate {
		t.Errorf("clearing the flag left the table behind:\n%s", got)
	}

	// nil leaves whatever is there.
	if got = mustEdit(t, mustEdit(t, scopeTemplate, on), coreOnly()); !strings.Contains(got, "global-client = true") {
		t.Errorf("an unset flag touched the file:\n%s", got)
	}
}

func TestEditScopeRefusesALayoutItCannotEdit(t *testing.T) {
	src := `[project]
name = "my-module"

[tool.uv]
workspace = { members = ["sdk"] }
`
	_, err := editScope([]byte(src), coreOnly())
	if err == nil {
		t.Error("an inline workspace table was edited silently")
	} else if !strings.Contains(err.Error(), workspaceForm) {
		t.Errorf("the refusal does not say what to write: %v", err)
	}
	for name, src := range map[string]string{
		"inline sources": "[project]\nname = \"x\"\n\n[tool.uv]\nsources = { dagger-io = { path = \"sdk\" } }\n",
		"dotted members": "[project]\nname = \"x\"\n\n[tool]\nuv.workspace.members = [\"sdk\"]\n",
	} {
		if _, err := editScope([]byte(src), coreOnly()); err == nil {
			t.Errorf("%s: edited silently", name)
		} else if !strings.Contains(err.Error(), workspaceForm) || !strings.Contains(err.Error(), sourcesForm) {
			t.Errorf("%s: the refusal does not say what to write: %v", name, err)
		}
	}
	if _, err := editScope([]byte("not = toml = at all\n"), coreOnly()); err == nil {
		t.Error("an unparsable file was edited")
	}
}

func TestEditScopeOwnsOnlyItsNames(t *testing.T) {
	src := `[project]
name = "my-module"
dependencies = ["dagger-io", "dagger-clients-core", "dagger-clients-linter>=0.0.0"]

[tool.uv.workspace]
members = ["sdk", "clients/core", "clients/linter/"]

[tool.uv.sources]
dagger-io = { workspace = true }
dagger-clients-core = { workspace = true }
"dagger-clients-linter" = { workspace = true }
`
	// A pinned dependency and a quoted or slash-terminated entry are the
	// same member, so nothing is added twice.
	if got := mustEdit(t, src, withLinter()); got != src {
		t.Errorf("entries were duplicated:\n%s", got)
	}
}

func TestEditScopeReadsNoHeaderInsideAValue(t *testing.T) {
	src := `[project]
name = "my-module"
dependencies = ["dagger-clients-core"]

[tool.mine]
snippet = """
[tool.uv.sources]
dagger-clients-fake = { workspace = true }
"""
matrix = [
  ["a", "b"],
]

[tool.uv.workspace]
members = ["sdk", "clients/core"]

[tool.uv.sources]
dagger-io = { workspace = true }
dagger-clients-core = { workspace = true }
`
	got := mustEdit(t, src, withLinter())
	want := strings.Replace(src, "members = [\"sdk\", \"clients/core\"]", "members = [\"sdk\", \"clients/core\", \"clients/linter\"]", 1)
	want = strings.Replace(want, "dependencies = [\"dagger-clients-core\"]", "dependencies = [\"dagger-clients-core\", \"dagger-clients-linter\"]", 1)
	want += "dagger-clients-linter = { workspace = true }\n"
	if got != want {
		t.Errorf("got:\n%s\nwant:\n%s", got, want)
	}
}

func TestEditScopeClearingTheFlagKeepsTheUsersComment(t *testing.T) {
	src := scopeTemplate + "\n[tool.dagger]\n# keep the global client until the lint module migrates\n"
	on := coreOnly()
	on.GlobalClient = boolPtr(true)
	off := coreOnly()
	off.GlobalClient = boolPtr(false)
	if got := mustEdit(t, mustEdit(t, src, on), off); got != src {
		t.Errorf("clearing the flag did not restore the file:\n%s", got)
	}
}

func TestEditScopeClearingTheFlagKeepsWhatFollowsTheTable(t *testing.T) {
	src := `[project]
name = "my-module"
dependencies = ["dagger-clients-core"]

[tool.dagger]
global-client = true

[tool.uv.workspace]
members = ["sdk", "clients/core"]

[tool.uv.sources]
dagger-io = { workspace = true }
dagger-clients-core = { workspace = true }


`
	off := coreOnly()
	off.GlobalClient = boolPtr(false)
	want := strings.Replace(src, "[tool.dagger]\nglobal-client = true\n\n", "", 1)
	if got := mustEdit(t, src, off); got != want {
		t.Errorf("got:\n%q\nwant:\n%q", got, want)
	}
}

func TestEditScopeEditsSourcesWrittenAsTables(t *testing.T) {
	src := `[project]
name = "my-module"
dependencies = ["dagger-io", "dagger-clients-core", "dagger-clients-old"]

[tool.uv.workspace]
members = ['sdk', 'clients/core', 'clients/old']

[tool.uv.sources.dagger-io]
path = "sdk"
editable = true

[tool.uv.sources."dagger-clients-core"]
workspace = true

[tool.uv.sources.dagger-clients-old]
workspace = true

[tool.uv.sources.mine]
path = "../mine"

[tool.mine]
answer = 42
`
	want := `[project]
name = "my-module"
dependencies = ["dagger-io", "dagger-clients-core", "dagger-clients-linter"]

[tool.uv.workspace]
members = ['sdk', 'clients/core', "clients/linter"]

[tool.uv.sources.dagger-io]
workspace = true

[tool.uv.sources."dagger-clients-core"]
workspace = true

[tool.uv.sources.mine]
path = "../mine"

[tool.uv.sources.dagger-clients-linter]
workspace = true

[tool.mine]
answer = 42
`
	edit := withLinter()
	edit.Stale = []string{"clients/old"}
	got := mustEdit(t, src, edit)
	if got != want {
		t.Errorf("got:\n%s\nwant:\n%s", got, want)
	}
	if again := mustEdit(t, got, withLinter()); again != got {
		t.Errorf("a second edit changed the file:\n%s", again)
	}
}

// A comment after an element's comma is on that element's line: removing the
// next element keeps it, and removing the element takes it along.
func TestEditScopeKeepsACommentWithItsLine(t *testing.T) {
	for name, tc := range map[string]struct{ src, want string }{
		"the next element goes": {
			src: `dependencies = [
  "httpx", # keep: this documents the user's dependency
  "dagger-clients-old",
]`,
			want: `dependencies = [
  "httpx", # keep: this documents the user's dependency
]`,
		},
		"the element goes with its comment": {
			src: `dependencies = [
  "httpx", # the user's
  "dagger-clients-old", # generated
  "rich",  # the user's too
]`,
			want: `dependencies = [
  "httpx", # the user's
  "rich",  # the user's too
]`,
		},
		"the last element, without a trailing comma": {
			src: `dependencies = [ # the bracket's
  "httpx", # the user's
  "dagger-clients-old"  # generated
]`,
			want: `dependencies = [ # the bracket's
  "httpx", # the user's
]`,
		},
	} {
		t.Run(name, func(t *testing.T) {
			src := "[project]\nname = \"x\"\n" + tc.src + "\n"
			edit := scopeEdit{Members: []string{"sdk"}, Sources: []string{"dagger-io"}}
			got := mustEdit(t, src, edit)
			want := "[project]\nname = \"x\"\n" + tc.want + "\n"
			if !strings.HasPrefix(got, want) {
				t.Errorf("got:\n%s\nwant it to start with:\n%s", got, want)
			}
		})
	}
}

func TestEditScopeAddsToAnEmptyMultiLineArray(t *testing.T) {
	src := "[project]\nname = \"x\"\ndependencies = [\n]\n"
	edit := scopeEdit{Members: []string{"sdk"}, Sources: []string{"dagger-io"}, Dependencies: []string{"dagger-clients-core"}}
	got := mustEdit(t, src, edit)
	if !strings.Contains(got, "dependencies = [\n    \"dagger-clients-core\",\n]\n") {
		t.Errorf("got:\n%s", got)
	}
}

func TestEditScopeReadsDottedSourceKeys(t *testing.T) {
	head := `[project]
name = "my-module"
dependencies = ["dagger-io", "dagger-clients-core"]

[tool.uv.workspace]
members = ["sdk", "clients/core"]

[tool.uv.sources]
`
	kept := head + `dagger-io.workspace = true
"dagger-clients-core" . workspace = true  # spaced and quoted
mine.path = "../mine"
`
	if got := mustEdit(t, kept, coreOnly()); got != kept {
		t.Errorf("a dotted workspace source was rewritten:\n%s", got)
	}

	vendored := head + `dagger-io.path = "sdk"
dagger-io.editable = true
dagger-clients-core.workspace = true
dagger-clients-old.workspace = true
mine.path = "../mine"
`
	want := head + `dagger-clients-core.workspace = true
mine.path = "../mine"
dagger-io = { workspace = true }
`
	if got := mustEdit(t, vendored, coreOnly()); got != want {
		t.Errorf("got:\n%s\nwant:\n%s", got, want)
	}
}

func TestRemoveTableKeepsEveryOtherByte(t *testing.T) {
	for name, tc := range map[string]struct {
		src  string
		want string
	}{
		"between tables": {
			"name = \"demo\"  # the user's comment\nengineVersion = 'v1.0.0'\n\n[entrypoint]\nkind = \"module\"\nsource = \"../runtime\"\n\n[runtime]\nsource = \"python\"\n",
			"name = \"demo\"  # the user's comment\nengineVersion = 'v1.0.0'\n\n[runtime]\nsource = \"python\"\n",
		},
		"last, with a quoted header": {
			"name = \"demo\"\n\n[ \"entrypoint\" ]  # static\nkind = \"dang\"\nsource = \"./sdk/entrypoint\"\n",
			"name = \"demo\"\n\n",
		},
		"a header inside a multiline string is no table": {
			"name = \"demo\"\nnotes = \"\"\"\n[entrypoint]\nkind = \"dang\"\n\"\"\"\n\n[entrypoint]\nkind = \"module\"\n",
			"name = \"demo\"\nnotes = \"\"\"\n[entrypoint]\nkind = \"dang\"\n\"\"\"\n\n",
		},
		"no such table": {
			"name = \"demo\"\n\n[runtime]\nsource = \"python\"\n",
			"name = \"demo\"\n\n[runtime]\nsource = \"python\"\n",
		},
	} {
		t.Run(name, func(t *testing.T) {
			got, err := removeTable([]byte(tc.src), "entrypoint")
			if err != nil {
				t.Fatal(err)
			}
			if string(got) != tc.want {
				t.Errorf("got:\n%s\nwant:\n%s", got, tc.want)
			}
		})
	}
}
