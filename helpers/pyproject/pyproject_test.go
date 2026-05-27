package main

import (
	"strings"
	"testing"
)

const sample = `[project]
name = "demo"
version = "0.1.0"
requires-python = ">=3.14"
dependencies = ["dagger-io"]

[build-system]
requires = ["uv_build>=0.8.4,<0.9.0"]
build-backend = "uv_build"

[tool.uv.sources]
dagger-io = { path = "sdk", editable = true }
`

const configured = `[project]
requires-python = ">=3.12"

[tool.dagger]
use-uv = false
base-image = "python:3.12-slim"
`

func mustLoad(t *testing.T, s string) map[string]any {
	t.Helper()
	doc, err := load([]byte(s))
	if err != nil {
		t.Fatalf("load: %v", err)
	}
	return doc
}

func TestGetPythonVersion(t *testing.T) {
	if got := getPythonVersion(mustLoad(t, sample)); got != "3.14" {
		t.Errorf("sample: got %q, want 3.14", got)
	}
	if got := getPythonVersion(mustLoad(t, configured)); got != "3.12" {
		t.Errorf("configured: got %q, want 3.12", got)
	}
	if got := getPythonVersion(mustLoad(t, "")); got != "" {
		t.Errorf("empty: got %q, want \"\"", got)
	}
}

func TestGetUseUv(t *testing.T) {
	if !getUseUv(mustLoad(t, sample)) {
		t.Error("sample: use-uv should default to true when absent")
	}
	if getUseUv(mustLoad(t, configured)) {
		t.Error("configured: use-uv should be false")
	}
}

func TestGetBaseImage(t *testing.T) {
	if got := getBaseImage(mustLoad(t, sample)); got != "" {
		t.Errorf("sample: got %q, want \"\"", got)
	}
	if got := getBaseImage(mustLoad(t, configured)); got != "python:3.12-slim" {
		t.Errorf("configured: got %q, want python:3.12-slim", got)
	}
}

func TestSetPythonVersionPreservesData(t *testing.T) {
	doc := mustLoad(t, sample)
	setPythonVersion(doc, "3.13")
	out, err := dump(doc)
	if err != nil {
		t.Fatalf("dump: %v", err)
	}
	s := string(out)
	if !strings.Contains(s, ">=3.13") {
		t.Errorf("missing new version in:\n%s", s)
	}
	if !strings.Contains(s, "dagger-io") || !strings.Contains(s, "uv_build") {
		t.Errorf("unrelated keys were dropped:\n%s", s)
	}
}

func TestSetUseUvFalseWritesKey(t *testing.T) {
	doc := mustLoad(t, sample)
	setUseUv(doc, false)
	out, err := dump(doc)
	if err != nil {
		t.Fatalf("dump: %v", err)
	}
	if !strings.Contains(string(out), "use-uv = false") {
		t.Errorf("missing use-uv = false in:\n%s", out)
	}
}

func TestSetUseUvTrueRemovesKey(t *testing.T) {
	doc := mustLoad(t, configured)
	setUseUv(doc, true)
	if getUseUv(doc) != true {
		t.Error("use-uv should read back as true after reset")
	}
	// removing use-uv must not drop the sibling base-image key.
	if got := getBaseImage(doc); got != "python:3.12-slim" {
		t.Errorf("setUseUv(true) clobbered base-image, got %q", got)
	}
	out, err := dump(doc)
	if err != nil {
		t.Fatalf("dump: %v", err)
	}
	if strings.Contains(string(out), "use-uv") {
		t.Errorf("use-uv key should be removed when set to default true:\n%s", out)
	}
}

func TestSetBaseImage(t *testing.T) {
	doc := mustLoad(t, sample)
	setBaseImage(doc, "python:3.13-slim")
	if got := getBaseImage(doc); got != "python:3.13-slim" {
		t.Errorf("got %q", got)
	}
}

func TestUnsetBaseImageRoundTrips(t *testing.T) {
	doc := mustLoad(t, configured)
	unsetBaseImage(doc)
	if got := getBaseImage(doc); got != "" {
		t.Errorf("base-image should be unset, got %q", got)
	}
	// unsetting base-image must not remove the other [tool.dagger] key.
	if getUseUv(doc) != false {
		t.Error("unsetBaseImage clobbered use-uv")
	}
}

func TestUnsetLastDaggerKeyRemovesTable(t *testing.T) {
	doc := mustLoad(t, `[tool.dagger]
base-image = "x"
`)
	unsetBaseImage(doc)
	out, err := dump(doc)
	if err != nil {
		t.Fatalf("dump: %v", err)
	}
	if strings.Contains(string(out), "tool.dagger") || strings.Contains(string(out), "[tool]") {
		t.Errorf("empty tables should be pruned:\n%s", out)
	}
}
