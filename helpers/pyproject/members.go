package main

import (
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strings"
)

// generatedKinds are the values of [tool.dagger] generated the SDK writes. A
// member marked with anything else is a file the SDK does not understand, so
// it is never the SDK's to delete or to write over.
var generatedKinds = map[string]bool{"client": true, "core": true, "runtime": true}

// generatedKind reads the marker of a member's pyproject.toml as TOML: a
// string that merely looks like the table is not the table. Empty when the
// file does not parse, has no marker, or has one of an unknown kind.
func generatedKind(data []byte) string {
	doc, err := load(data)
	if err != nil {
		return ""
	}
	kind, _ := table(table(doc, "tool"), "dagger")["generated"].(string)
	if !generatedKinds[kind] {
		return ""
	}
	return kind
}

// memberKinds lists the members of a scope that carry a known marker, one
// "<member> <kind>" line each: sdk/ and every directory under clients/.
func memberKinds(scope string) (string, error) {
	candidates := []string{"sdk"}
	entries, err := os.ReadDir(filepath.Join(scope, "clients"))
	if err != nil && !os.IsNotExist(err) {
		return "", err
	}
	for _, e := range entries {
		if e.IsDir() {
			candidates = append(candidates, "clients/"+e.Name())
		}
	}
	sort.Strings(candidates)
	var out strings.Builder
	for _, member := range candidates {
		data, err := os.ReadFile(filepath.Join(scope, member, "pyproject.toml"))
		if err != nil {
			if os.IsNotExist(err) {
				continue
			}
			return "", err
		}
		if kind := generatedKind(data); kind != "" {
			fmt.Fprintf(&out, "%s %s\n", member, kind)
		}
	}
	return out.String(), nil
}
