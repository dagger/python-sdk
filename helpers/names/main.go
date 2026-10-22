// Command names writes the names file the Python code generator reads: the
// schema's names in snake_case, as the engine formats them.
//
// The engine names everything in a schema served at v1.0.0-beta.21 or later
// by its naming rules, and from then on its Query.formatIdentifiers tells how
// to write a schema name in any casing. Generation reads schemas from
// introspection JSON, without a session in the view of the schema it
// generates (python-sdk itself is served before that version), so it can't
// call it. This formats the names with a copy of the engine's own naming
// package instead (see naming/pin.go), keyed by the schema's __schemaVersion like
// formatIdentifiers(version:).
//
// The gate is the one formatIdentifiers has: a schema without it gets an empty
// names file, and the generator keeps its legacy conversion.
//
// usage: names -o <names.json> <introspection.json>...
package main

import (
	"encoding/json"
	"flag"
	"fmt"
	"io"
	"os"
	"sort"
	"strings"

	"golang.org/x/mod/semver"

	"names/naming"
)

// SnakeFormat is the key of the names file's snake_case names: the CASING and
// ACRONYMS that formatIdentifiers would be called with. The acronym style
// doesn't change snake_case.
const SnakeFormat = "SNAKE:UPPERCASE"

// CoveredVersion is the newest engine release line whose dictionaries the copy
// of engine/naming in naming/ is known to hold. A dictionary addition is keyed
// by the engine version that introduced it, so a schema newer than this may be
// named by a dictionary the copy doesn't have. Bump it with the copy (see
// naming/pin.go).
const CoveredVersion = "v1.0.0"

func main() {
	if err := run(os.Args[1:], os.Stdout, os.Stderr); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}

func run(args []string, stdout, stderr io.Writer) error {
	flags := flag.NewFlagSet("names", flag.ContinueOnError)
	flags.SetOutput(stderr)
	output := flags.String("o", "", "path to write the names file to")
	if err := flags.Parse(args); err != nil {
		return err
	}
	if *output == "" || flags.NArg() == 0 {
		return fmt.Errorf("usage: names -o <names.json> <introspection.json>...")
	}

	schemas := make([]schema, 0, flags.NArg())
	for _, path := range flags.Args() {
		data, err := os.ReadFile(path)
		if err != nil {
			return err
		}
		s, err := parseSchema(data)
		if err != nil {
			return fmt.Errorf("%s: %w", path, err)
		}
		schemas = append(schemas, s)
	}

	file, warnings := namesFile(schemas)
	for _, w := range warnings {
		fmt.Fprintln(stderr, "WARNING:", w)
	}
	data, err := json.MarshalIndent(file, "", "  ")
	if err != nil {
		return err
	}
	if err := os.WriteFile(*output, append(data, '\n'), 0o644); err != nil {
		return err
	}
	fmt.Fprintf(stdout, "Names written to %s\n", *output)
	return nil
}

// File is the names file: each CASING:ACRONYMS format to the names in it, by
// schema name. It has no format at all for schemas without
// Query.formatIdentifiers.
type File map[string]map[string]string

// namesFile formats the names of the schemas that have Query.formatIdentifiers.
// A name the engine can't parse (non-ASCII, say) is left out, and the
// generator converts it the old way, like a name the engine leaves as it is.
func namesFile(schemas []schema) (File, []string) {
	file := File{}
	var warnings []string
	for _, s := range schemas {
		if !s.hasFormatIdentifiers() {
			continue
		}
		if w := coverageWarning(s.Version); w != "" {
			warnings = append(warnings, w)
		}
		dict := naming.DictionaryFor(s.Version)
		snake := file[SnakeFormat]
		if snake == nil {
			snake = map[string]string{}
			file[SnakeFormat] = snake
		}
		for _, name := range s.names() {
			id, err := dict.Parse(name)
			if err != nil {
				continue
			}
			snake[name] = id.Format(naming.Snake, naming.Uppercase)
		}
	}
	return file, warnings
}

// coverageWarning says when a schema may be named by a dictionary newer than
// the copy's. It doesn't fail generation: dictionary additions are rare,
// engine releases are not, and the names only differ where an added term
// occurs.
func coverageWarning(version string) string {
	v := version
	if v != "" && !strings.HasPrefix(v, "v") {
		v = "v" + v
	}
	if !semver.IsValid(v) {
		return ""
	}
	base := strings.TrimSuffix(strings.TrimSuffix(v, semver.Build(v)), semver.Prerelease(v))
	if semver.Compare(base, CoveredVersion) <= 0 {
		return ""
	}
	return fmt.Sprintf(
		"the schema's version %s is newer than %s, the newest engine this "+
			"Python SDK's copy of the engine's naming rules covers: names the "+
			"engine added acronyms or terms for since may be written "+
			"differently than the engine writes them. Upgrade the Python SDK.",
		version, CoveredVersion)
}

// schema is what the names file needs of an introspection result.
type schema struct {
	Version string `json:"__schemaVersion"`
	Schema  struct {
		QueryType *struct {
			Name string `json:"name"`
		} `json:"queryType"`
		Types []schemaType `json:"types"`
	} `json:"__schema"`
}

type schemaType struct {
	Name   string `json:"name"`
	Fields []struct {
		Name string `json:"name"`
		Args []struct {
			Name string `json:"name"`
		} `json:"args"`
	} `json:"fields"`
	InputFields []struct {
		Name string `json:"name"`
	} `json:"inputFields"`
	EnumValues []struct {
		Name string `json:"name"`
	} `json:"enumValues"`
}

func parseSchema(data []byte) (schema, error) {
	var s schema
	if err := json.Unmarshal(data, &s); err != nil {
		return s, err
	}
	if len(s.Schema.Types) == 0 {
		return s, fmt.Errorf("not an introspection result: no __schema types")
	}
	return s, nil
}

// hasFormatIdentifiers reports whether the schema has Query.formatIdentifiers,
// which is in the schemas the engine names by its naming rules, and only in
// those.
func (s schema) hasFormatIdentifiers() bool {
	query := "Query"
	if s.Schema.QueryType != nil && s.Schema.QueryType.Name != "" {
		query = s.Schema.QueryType.Name
	}
	for _, t := range s.Schema.Types {
		if t.Name != query {
			continue
		}
		for _, f := range t.Fields {
			if f.Name == "formatIdentifiers" {
				return true
			}
		}
	}
	return false
}

// names lists the schema's own names, sorted: its types, fields, arguments,
// input fields and enum values. Introspection's own (__Type) are left out.
func (s schema) names() []string {
	seen := map[string]bool{}
	add := func(name string) {
		if name != "" && !strings.HasPrefix(name, "__") {
			seen[name] = true
		}
	}
	for _, t := range s.Schema.Types {
		if strings.HasPrefix(t.Name, "__") {
			continue
		}
		add(t.Name)
		for _, f := range t.Fields {
			add(f.Name)
			for _, a := range f.Args {
				add(a.Name)
			}
		}
		for _, f := range t.InputFields {
			add(f.Name)
		}
		for _, v := range t.EnumValues {
			add(v.Name)
		}
	}
	names := make([]string, 0, len(seen))
	for name := range seen {
		names = append(names, name)
	}
	sort.Strings(names)
	return names
}
