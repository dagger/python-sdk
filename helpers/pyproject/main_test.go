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

func writeScript(t *testing.T, contents string) string {
	t.Helper()
	p := filepath.Join(t.TempDir(), "main.py")
	if err := os.WriteFile(p, []byte(contents), 0o644); err != nil {
		t.Fatal(err)
	}
	return p
}

// The code after the block, the comments in it, and a `# ` line, which
// reads as an empty line, keep their bytes.
const scriptModule = `#!/usr/bin/env python
# /// script
# requires-python = ">=3.14"
# dependencies = ["dagger-io", "dagger-clients-core"]
# 
# [tool.uv.sources]
#   # the user's comment
# dagger-io = { path = "sdk" }
# dagger-clients-core = { path = "clients/core" }
# ///

from dagger import function, object_type


@object_type
class App:
    @function
    def hello(self) -> str:
        return "# /// script"
`

var scriptEditArgs = []string{
	"--path-sources",
	"--source", "dagger-io=sdk", "--source", "dagger-clients-core=clients/core",
	"--dependency", "dagger-clients-core",
}

func TestRunEditScopeEditsAScriptBlock(t *testing.T) {
	p := writeScript(t, scriptModule)
	if err := run(append([]string{"edit-scope", p}, scriptEditArgs...)); err != nil {
		t.Fatalf("edit-scope: %v", err)
	}
	if data, _ := os.ReadFile(p); string(data) != scriptModule {
		t.Fatalf("a matching block was rewritten:\n%s", data)
	}

	err := run(append([]string{"edit-scope", p}, append(scriptEditArgs,
		"--source", "dagger-clients-linter=clients/linter", "--dependency", "dagger-clients-linter")...))
	if err != nil {
		t.Fatalf("edit-scope: %v", err)
	}
	want := strings.NewReplacer(
		`"dagger-clients-core"]`, `"dagger-clients-core", "dagger-clients-linter"]`,
		"# dagger-clients-core = { path = \"clients/core\" }\n",
		"# dagger-clients-core = { path = \"clients/core\" }\n# dagger-clients-linter = { path = \"clients/linter\" }\n",
	).Replace(scriptModule)
	data, _ := os.ReadFile(p)
	if string(data) != want {
		t.Fatalf("got:\n%s\nwant:\n%s", data, want)
	}
	if strings.Contains(string(data), "workspace") {
		t.Errorf("a script got a workspace:\n%s", data)
	}

	if err := run(append([]string{"edit-scope", p}, scriptEditArgs...)); err != nil {
		t.Fatalf("edit-scope: %v", err)
	}
	if data, _ := os.ReadFile(p); string(data) != scriptModule {
		t.Errorf("removing the client did not give the file back:\n%s", data)
	}
}

// A file saved with CRLF line ends keeps them, in the lines the edit adds
// too, and a last line without its line end stays without one.
func TestRunEditScopeEditsACRLFScriptBlock(t *testing.T) {
	crlf := strings.TrimSuffix(strings.ReplaceAll(scriptModule, "\n", "\r\n"), "\r\n")
	p := writeScript(t, crlf)
	if got := printed(t, "get-python-version", p); got != "3.14" {
		t.Fatalf("get-python-version: got %q", got)
	}
	if err := run(append([]string{"edit-scope", p}, scriptEditArgs...)); err != nil {
		t.Fatalf("edit-scope: %v", err)
	}
	if data, _ := os.ReadFile(p); string(data) != crlf {
		t.Fatalf("a matching block was rewritten:\n%q", data)
	}

	err := run(append([]string{"edit-scope", p}, append(scriptEditArgs,
		"--source", "dagger-clients-linter=clients/linter", "--dependency", "dagger-clients-linter")...))
	if err != nil {
		t.Fatalf("edit-scope: %v", err)
	}
	want := strings.NewReplacer(
		`"dagger-clients-core"]`, `"dagger-clients-core", "dagger-clients-linter"]`,
		"# dagger-clients-core = { path = \"clients/core\" }\r\n",
		"# dagger-clients-core = { path = \"clients/core\" }\r\n# dagger-clients-linter = { path = \"clients/linter\" }\r\n",
	).Replace(crlf)
	if data, _ := os.ReadFile(p); string(data) != want {
		t.Fatalf("got:\n%q\nwant:\n%q", data, want)
	}

	if err := run(append([]string{"edit-scope", p}, scriptEditArgs...)); err != nil {
		t.Fatalf("edit-scope: %v", err)
	}
	if data, _ := os.ReadFile(p); string(data) != crlf {
		t.Errorf("removing the client did not give the file back:\n%q", data)
	}
}

// A block with no content line is a block, since every field is optional.
// Setting the global client and clearing it gives the file back, and an edit
// writes into the block with the file's line ends.
func TestRunEditsAnEmptyScriptBlock(t *testing.T) {
	for name, nl := range map[string]string{"LF": "\n", "CRLF": "\r\n"} {
		t.Run(name, func(t *testing.T) {
			src := "# /// script" + nl + "# ///" + nl + "print('hello')"
			p := writeScript(t, src)
			if got := printed(t, "get-path-sources", p); got != "" {
				t.Fatalf("get-path-sources: got %q", got)
			}
			if err := run([]string{"set-global-client", p, "true"}); err != nil {
				t.Fatalf("set: %v", err)
			}
			want := "# /// script" + nl + "# [tool.dagger]" + nl + "# global-client = true" + nl + "# ///" + nl + "print('hello')"
			if data, _ := os.ReadFile(p); string(data) != want {
				t.Fatalf("got:\n%q\nwant:\n%q", data, want)
			}
			if err := run([]string{"set-global-client", p, "false"}); err != nil {
				t.Fatalf("clear: %v", err)
			}
			if data, _ := os.ReadFile(p); string(data) != src {
				t.Fatalf("clearing the flag did not give the empty block back:\n%q", data)
			}

			if err := run(append([]string{"edit-scope", p}, scriptEditArgs...)); err != nil {
				t.Fatalf("edit-scope: %v", err)
			}
			if got := printed(t, "get-path-sources", p); got != "clients/core\nsdk\n" {
				t.Errorf("get-path-sources after edit-scope: got %q", got)
			}
			data, _ := os.ReadFile(p)
			if lines := strings.Split(string(data), nl); len(lines) < 4 || strings.Contains(strings.Join(lines, ""), "\n") {
				t.Errorf("edit-scope wrote other line ends than %q:\n%q", nl, data)
			}
		})
	}
}

func TestRunEditScopeTakesPathSourcesAsNameAndDirectory(t *testing.T) {
	p := writeScript(t, scriptModule)
	if err := run([]string{"edit-scope", p, "--path-sources", "--source", "dagger-io"}); err == nil {
		t.Error("expected an error for a path source without its directory")
	}
}

func TestRunReadsAndSetsAScriptBlock(t *testing.T) {
	p := writeScript(t, scriptModule)
	if got := printed(t, "get-python-version", p); got != "3.14" {
		t.Errorf("get-python-version: got %q", got)
	}
	if got := printed(t, "get-path-sources", p); got != "clients/core\nsdk\n" {
		t.Errorf("get-path-sources: got %q", got)
	}
	if err := run([]string{"set-python-version", p, "3.13"}); err != nil {
		t.Fatalf("set: %v", err)
	}
	data, _ := os.ReadFile(p)
	block, err := readScriptBlock(p, data)
	if err != nil {
		t.Fatalf("the block does not read after set: %v\n%s", err, data)
	}
	doc := mustLoad(t, string(block.toml()))
	if doc["requires-python"] != ">=3.13" || table(doc, "project") != nil {
		t.Errorf("requires-python not set at the top of the block:\n%s", data)
	}
	if !strings.HasPrefix(string(data), "#!/usr/bin/env python\n# /// script\n") ||
		!strings.HasSuffix(string(data), scriptModule[strings.Index(scriptModule, "# ///\n"):]) {
		t.Errorf("set touched the file outside the block:\n%s", data)
	}
}

func TestRunRefusesAScriptWithoutOneBlock(t *testing.T) {
	other := "# /// pyproject\n# [project]\n# name = \"x\"\n# ///\n"
	block := "# /// script\n# dependencies = []\n# ///\n"
	for name, tc := range map[string]struct {
		src  string
		want string
	}{
		"no block":          {"print('hello')\n", "no `# /// script` metadata block"},
		"another type":      {other + "print('hello')\n", "no `# /// script` metadata block"},
		"two script blocks": {block + "\n" + block, "2 `# /// script` metadata blocks"},
		"unclosed block":    {"# /// script\n# dependencies = []\nprint('hello')\n", "no `# /// script` metadata block"},
		"two empty blocks":  {"# /// script\n# ///\n\n# /// script\n# ///\n", "2 `# /// script` metadata blocks"},
	} {
		t.Run(name, func(t *testing.T) {
			p := writeScript(t, tc.src)
			err := run([]string{"get-python-version", p})
			if err == nil || !strings.Contains(err.Error(), tc.want) {
				t.Errorf("got %v, want an error containing %q", err, tc.want)
			}
			if data, _ := os.ReadFile(p); string(data) != tc.src {
				t.Errorf("a refused file was written:\n%s", data)
			}
		})
	}
	// Comment lines that run on are one block, so a blank line parts the two.
	p := writeScript(t, other+"\n"+block)
	if got := printed(t, "get-path-sources", p); got != "" {
		t.Errorf("a block of another type was read: %q", got)
	}
}
