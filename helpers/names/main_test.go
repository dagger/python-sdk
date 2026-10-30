package main

import (
	"bytes"
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func introspection(version string, queryFields ...string) string {
	fields := make([]string, 0, len(queryFields))
	for _, f := range queryFields {
		fields = append(fields, `{"name": "`+f+`", "args": []}`)
	}
	return `{
		"__schemaVersion": "` + version + `",
		"__schema": {
			"queryType": {"name": "Query"},
			"types": [
				{"name": "Query", "fields": [` + strings.Join(fields, ",") + `]},
				{"name": "GitCommit", "fields": [
					{"name": "shortSHA", "args": []},
					{"name": "parentSHAs", "args": []}
				]},
				{"name": "GitRepository", "fields": [
					{"name": "withRemote", "args": [{"name": "pushURL"}, {"name": "café"}]}
				]},
				{"name": "LLMContentBlockInput", "inputFields": [{"name": "callID"}]},
				{"name": "ImageMediaTypes", "enumValues": [{"name": "OCI"}, {"name": "OCIMediaTypes"}]},
				{"name": "__Type", "fields": [{"name": "__internal", "args": []}]}
			]
		}
	}`
}

func generate(t *testing.T, schemas ...string) (File, string) {
	t.Helper()
	return generateOn(t, "", schemas...)
}

func generateOn(t *testing.T, engine string, schemas ...string) (File, string) {
	t.Helper()
	dir := t.TempDir()
	args := []string{"-o", filepath.Join(dir, "names.json")}
	if engine != "" {
		args = append(args, "-engine", engine)
	}
	for i, s := range schemas {
		path := filepath.Join(dir, "schema"+string(rune('a'+i))+".json")
		if err := os.WriteFile(path, []byte(s), 0o644); err != nil {
			t.Fatal(err)
		}
		args = append(args, path)
	}
	var stdout, stderr bytes.Buffer
	if err := run(args, &stdout, &stderr); err != nil {
		t.Fatal(err)
	}
	data, err := os.ReadFile(filepath.Join(dir, "names.json"))
	if err != nil {
		t.Fatal(err)
	}
	var file File
	if err := json.Unmarshal(data, &file); err != nil {
		t.Fatal(err)
	}
	return file, stderr.String()
}

func TestNamesWithFormatIdentifiers(t *testing.T) {
	file, warnings := generate(t, introspection("v1.0.0-beta.21", "formatIdentifiers", "gitRepository"))
	if warnings != "" {
		t.Errorf("unexpected warnings: %s", warnings)
	}
	want := map[string]string{
		"Query":                "query",
		"formatIdentifiers":    "format_identifiers",
		"gitRepository":        "git_repository",
		"GitCommit":            "git_commit",
		"shortSHA":             "short_sha",
		"parentSHAs":           "parent_shas",
		"GitRepository":        "git_repository",
		"withRemote":           "with_remote",
		"pushURL":              "push_url",
		"LLMContentBlockInput": "llm_content_block_input",
		"callID":               "call_id",
		"ImageMediaTypes":      "image_media_types",
		"OCI":                  "oci",
		"OCIMediaTypes":        "oci_media_types",
	}
	snake := file[SnakeFormat]
	for name, formatted := range want {
		if got := snake[name]; got != formatted {
			t.Errorf("%s: got %q, want %q", name, got, formatted)
		}
	}
	// Non-ASCII names don't parse: left to the generator's own conversion.
	if _, ok := snake["café"]; ok {
		t.Error("café should be left out")
	}
	// Introspection's own names aren't the schema's.
	if _, ok := snake["__internal"]; ok {
		t.Error("__internal should be left out")
	}
	if len(snake) != len(want) {
		t.Errorf("got %d names, want %d: %v", len(snake), len(want), snake)
	}
}

// Released engines before v1.0.0-beta.21 serve formatIdentifiers in every v1
// view, but name modules by their legacy rules.
func TestNamesEngineGate(t *testing.T) {
	schema := introspection("v1.0.0", "formatIdentifiers", "gitRepository")
	for engine, names := range map[string]bool{
		"v1.0.0-beta.19":          false,
		"v1.0.0-beta.20+0da36c8d": false,
		"v1.0.0-beta.21":          true,
		"v1.0.0-beta.22":          true,
		"v1.0.0-dev-0123456789ab": true,
		"v1.0.0":                  true,
		"devel":                   true,
	} {
		file, _ := generateOn(t, engine, schema)
		if got := len(file) > 0; got != names {
			t.Errorf("engine %s: names %v, want %v", engine, got, names)
		}
	}
}

// The pinned dictionary: K8s is a term of its own, however it's written.
func TestNamesDictionary(t *testing.T) {
	schema := strings.Replace(introspection("v1.0.0-beta.21", "formatIdentifiers"), "withRemote", "withK8SCluster", 1)
	file, _ := generate(t, schema)
	if got := file[SnakeFormat]["withK8SCluster"]; got != "with_k8s_cluster" {
		t.Errorf("withK8SCluster: got %q", got)
	}
}

// A schema without Query.formatIdentifiers is named by the legacy rules: the
// file has no format, and the generator keeps its own conversion.
func TestNamesWithoutFormatIdentifiers(t *testing.T) {
	file, _ := generate(t, introspection("v1.0.0-beta.20", "gitRepository"))
	if len(file) != 0 {
		t.Errorf("got %v, want an empty file", file)
	}
}

// The names of every schema of a scope, for the global client.
func TestNamesOfSeveralSchemas(t *testing.T) {
	other := strings.Replace(introspection("v1.0.0-beta.21", "formatIdentifiers"), "withRemote", "withHTTPClient", 1)
	file, _ := generate(t, introspection("v1.0.0-beta.21", "formatIdentifiers"), other)
	snake := file[SnakeFormat]
	if snake["withRemote"] != "with_remote" || snake["withHTTPClient"] != "with_http_client" {
		t.Errorf("got %v", snake)
	}
}

// The generator writes an enum value's member as its snake_case name,
// uppercased: the names file has no SCREAMING_SNAKE map. That is the
// engine's SCREAMING_SNAKE for every name of its test vectors.
func TestScreamingSnakeIsUppercasedSnake(t *testing.T) {
	data, err := os.ReadFile("naming/testdata/vectors.json")
	if err != nil {
		t.Fatal(err)
	}
	var vectors []struct {
		Input   string            `json:"input"`
		Formats map[string]string `json:"formats"`
	}
	if err := json.Unmarshal(data, &vectors); err != nil {
		t.Fatal(err)
	}
	for _, v := range vectors {
		if got, want := strings.ToUpper(v.Formats["SNAKE"]), v.Formats["SCREAMING_SNAKE"]; got != want {
			t.Errorf("%q: uppercased snake_case %q, SCREAMING_SNAKE %q", v.Input, got, want)
		}
	}
}

func TestCoverageWarning(t *testing.T) {
	for version, warns := range map[string]bool{
		"v1.0.0-beta.21":       false,
		"v1.0.0":               false,
		"v1.0.0-dev-123":       false,
		"v1.0.0-beta.21+abcde": false,
		"":                     false,
		"garbage":              false,
		"v1.0.1":               true,
		"v1.1.0-beta.1":        true,
		"2.0.0":                true,
	} {
		if got := coverageWarning(version) != ""; got != warns {
			t.Errorf("%q: warns %v, want %v", version, got, warns)
		}
	}
	_, warnings := generate(t, introspection("v1.1.0", "formatIdentifiers"))
	if !strings.Contains(warnings, "WARNING:") || !strings.Contains(warnings, "v1.1.0") {
		t.Errorf("got warnings %q", warnings)
	}
}
