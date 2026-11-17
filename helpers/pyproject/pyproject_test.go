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
	dagger := table(table(doc, "tool"), "dagger")
	if _, ok := dagger["use-uv"]; ok {
		t.Error("use-uv should be unset after reset to the default true")
	}
	// removing use-uv must not drop the sibling base-image key.
	if got := dagger["base-image"]; got != "python:3.12-slim" {
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

func TestSetUseUvTruePrunesEmptyTable(t *testing.T) {
	doc := mustLoad(t, `[tool.dagger]
use-uv = false
`)
	setUseUv(doc, true)
	out, err := dump(doc)
	if err != nil {
		t.Fatalf("dump: %v", err)
	}
	if strings.Contains(string(out), "tool.dagger") || strings.Contains(string(out), "[tool]") {
		t.Errorf("empty tables should be pruned:\n%s", out)
	}
}

func TestSetBaseImage(t *testing.T) {
	doc := mustLoad(t, sample)
	setBaseImage(doc, "python:3.13-slim")
	if got := table(table(doc, "tool"), "dagger")["base-image"]; got != "python:3.13-slim" {
		t.Errorf("got %q", got)
	}
}
