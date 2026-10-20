package main

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestPythonFloorIsTheSDKs(t *testing.T) {
	data, err := os.ReadFile(filepath.Join("..", "..", "sdk", "pyproject.toml"))
	if err != nil {
		t.Fatal(err)
	}
	doc, err := load(data)
	if err != nil {
		t.Fatal(err)
	}
	if got := table(doc, "project")["requires-python"]; got != ">="+pythonFloor {
		t.Errorf("sdk/pyproject.toml requires-python is %q, pythonFloor is %q", got, pythonFloor)
	}
}

func TestBelowPythonFloor(t *testing.T) {
	for v, want := range map[string]bool{
		"3.11":      true,
		"3.11.9":    true,
		"2.7":       true,
		"2":         true,
		"3.12.0rc1": true,
		"3.12rc1":   true,
		"3.12.dev0": true,
		"3.12":      false,
		"3.12.0":    false,
		"3.12.1rc1": false,
		"3.13.0b2":  false,
		"3":         false,
		"4":         false,
		"pypy3.10":  false,
		"":          false,
	} {
		if got := below(v, pythonFloor); got != want {
			t.Errorf("below(%q, %q) = %v, want %v", v, pythonFloor, got, want)
		}
	}
}

func moduleDir(t *testing.T, files map[string]string) string {
	t.Helper()
	dir := t.TempDir()
	for name, contents := range files {
		if err := os.WriteFile(filepath.Join(dir, name), []byte(contents), 0o644); err != nil {
			t.Fatal(err)
		}
	}
	return dir
}

func TestPythonRefusal(t *testing.T) {
	project := func(requires string) string {
		return "[project]\nname = \"app\"\nrequires-python = \"" + requires + "\"\n"
	}
	for name, tc := range map[string]struct {
		files map[string]string
		want  string
	}{
		"floor":                     {map[string]string{"pyproject.toml": project(">=3.12")}, ""},
		"newer":                     {map[string]string{"pyproject.toml": project(">=3.14")}, ""},
		"older floor":               {map[string]string{"pyproject.toml": project(">=3.11")}, `this Python SDK needs Python 3.12 or later, and requires-python = ">=3.11" asks for Python 3.11. Ask for Python 3.12 or later, or pin the last release of this SDK that runs on it: dagger module install github.com/dagger/python-sdk@v1.2.0`},
		"older range":               {map[string]string{"pyproject.toml": project(">=3.10,<4")}, "asks for Python 3.10."},
		"older exact":               {map[string]string{"pyproject.toml": project("==3.11.4")}, "asks for Python 3.11.4."},
		"another major":             {map[string]string{"pyproject.toml": project(">=2.7")}, "asks for Python 2.7."},
		"older major only":          {map[string]string{"pyproject.toml": project(">=2")}, "asks for Python 2."},
		"range the runtime ignores": {map[string]string{"pyproject.toml": project("~=3.11")}, ""},
		"no requires-python":        {map[string]string{"pyproject.toml": "[project]\nname = \"app\"\n"}, ""},
		"no pyproject.toml":         {map[string]string{}, ""},
		"older script":              {map[string]string{"main.py": "# /// script\n# requires-python = \">=3.11\"\n# ///\n"}, "asks for Python 3.11."},
		"pyproject.toml over script": {
			map[string]string{"pyproject.toml": project(">=3.14"), "main.py": "# /// script\n# requires-python = \">=3.11\"\n# ///\n"},
			"",
		},
		"main.py without a block": {map[string]string{"main.py": "print('hello')\n"}, ""},
		"older pin": {
			map[string]string{".python-version": "3.11.9\n", "pyproject.toml": project(">=3.14")},
			"this Python SDK needs Python 3.12 or later, and .python-version pins Python 3.11.9. Set it to Python 3.12 or later, or pin the last release",
		},
		"pin over requires-python": {map[string]string{".python-version": "3.13\n", "pyproject.toml": project(">=3.10")}, ""},
		"base image over requires-python": {
			map[string]string{"pyproject.toml": project(">=3.10") + "\n[tool.dagger]\nbase-image = \"python:3.14-slim\"\n"},
			"",
		},
		"base image over pin": {
			map[string]string{".python-version": "3.11\n", "pyproject.toml": "[tool.dagger]\nbase-image = \"python:3.14-slim\"\n"},
			"",
		},
		"unreadable pin": {map[string]string{".python-version": "pypy3.10\n"}, ""},
	} {
		t.Run(name, func(t *testing.T) {
			got, err := pythonRefusal(moduleDir(t, tc.files))
			if err != nil {
				t.Fatal(err)
			}
			if tc.want == "" && got != "" || !strings.Contains(got, tc.want) {
				t.Errorf("got %q, want %q", got, tc.want)
			}
		})
	}
}

func TestPythonRefusalOffersTheLastReleaseOnlyWhereItRuns(t *testing.T) {
	for requires, offered := range map[string]bool{">=3.11": true, ">=3.10": true, ">=3.9": false, ">=2.7": false} {
		got, err := pythonRefusal(moduleDir(t, map[string]string{"pyproject.toml": "[project]\nrequires-python = \"" + requires + "\"\n"}))
		if err != nil {
			t.Fatal(err)
		}
		if strings.Contains(got, "python-sdk@v1.2.0") != offered {
			t.Errorf("%s: got %q, want the last release offered: %v", requires, got, offered)
		}
	}
}

func TestRunPythonRefusalTakesADirectory(t *testing.T) {
	dir := moduleDir(t, map[string]string{"pyproject.toml": "[project]\nrequires-python = \">=3.10\"\n"})
	if got := printed(t, "python-refusal", dir); !strings.Contains(got, "needs Python 3.12 or later") {
		t.Errorf("got %q", got)
	}
}
