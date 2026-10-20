package main

import (
	"encoding/json"
	"flag"
	"fmt"
	"os"
	"strings"
)

func main() {
	if err := run(os.Args[1:]); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}

// run dispatches a subcommand. get-* commands print to stdout (no trailing
// newline). set-* commands edit the file in place and re-emit it.
// edit-scope and set-global-client edit the file in place and keep its
// formatting: they touch a scope file that generation also owns.
//
// get-member-kinds takes a scope directory instead of a file, and
// python-refusal a module directory.
//
// derive-resources takes a directory of pyproject.toml files at their
// workspace paths and a module directory in it, and prints as JSON the paths
// the module's build reads outside the module directory, each relative to the
// module directory ("pattern") and to the workspace root ("rooted").
//
// get-generated prints nothing for a file that does not parse: such a file is
// no member of the SDK's, which is all its callers ask.
//
// Every command that takes a file reads a .py file as its PEP 723 script
// block, and writes back only the lines of the block it changes. A script
// holds at its top level what a pyproject.toml holds under [project].
//
// usage: pyproject <command> <file> [value | flags]
func run(args []string) error {
	if len(args) < 2 {
		return fmt.Errorf("usage: pyproject <command> <file> [value | flags]")
	}
	cmd, path := args[0], args[1]
	if cmd == "get-member-kinds" {
		out, err := memberKinds(path)
		if err != nil {
			return err
		}
		fmt.Print(out)
		return nil
	}
	if cmd == "derive-resources" {
		module, err := value(args)
		if err != nil {
			return err
		}
		resources, err := deriveResources(path, module)
		if err != nil {
			return err
		}
		out, err := json.Marshal(resources)
		if err != nil {
			return err
		}
		fmt.Print(string(out))
		return nil
	}
	if cmd == "python-refusal" {
		out, err := pythonRefusal(path)
		if err != nil {
			return err
		}
		fmt.Print(out)
		return nil
	}

	data, err := os.ReadFile(path)
	if err != nil {
		return err
	}
	var block *scriptBlock
	if isScript(path) {
		if block, err = readScriptBlock(path, data); err != nil {
			return err
		}
		data = block.toml()
	}
	write := func(out []byte) error {
		if string(out) == string(data) {
			return nil
		}
		if block != nil {
			out = block.embed(out)
		}
		return os.WriteFile(path, out, 0o644)
	}
	switch cmd {
	case "edit-scope":
		return runEditScope(data, args[2:], block != nil, write)
	case "set-global-client":
		return runSetGlobalClient(data, args, write)
	case "remove-table":
		return runRemoveTable(data, args, write)
	case "get-generated":
		fmt.Print(generatedKind(data))
		return nil
	}
	doc, err := load(data)
	if err != nil {
		return err
	}
	if block != nil {
		doc = asProject(doc)
	}

	switch cmd {
	case "get-python-version":
		fmt.Print(getPythonVersion(doc))
		return nil
	case "get-use-uv":
		// Print nothing when unset so callers can tell an absent setting from
		// an explicit value, instead of guessing the true default.
		if v, ok := getUseUv(doc); ok {
			fmt.Print(boolStr(v))
		}
		return nil
	case "get-base-image":
		fmt.Print(getBaseImage(doc))
		return nil
	case "get-members":
		fmt.Print(getMembers(doc))
		return nil
	case "get-path-sources":
		fmt.Print(getPathSources(doc))
		return nil
	case "get-global-client":
		if v, ok := getGlobalClient(doc); ok {
			fmt.Print(boolStr(v))
		}
		return nil
	case "set-python-version":
		v, err := value(args)
		if err != nil {
			return err
		}
		setPythonVersion(doc, v)
	case "set-use-uv":
		v, err := value(args)
		if err != nil {
			return err
		}
		setUseUv(doc, v == "true")
	case "set-base-image":
		v, err := value(args)
		if err != nil {
			return err
		}
		setBaseImage(doc, v)
	default:
		return fmt.Errorf("unknown command: %s", cmd)
	}

	if block != nil {
		doc = fromProject(doc)
	}
	out, err := dump(doc)
	if err != nil {
		return err
	}
	return write(out)
}

// runEditScope sets the SDK-owned entries of a scope pyproject.toml or
// script, and leaves the file alone when nothing changes. With
// --path-sources, each source names its member directory, as S=DIR, and is
// written as a path source; no workspace is written, since a script cannot
// be a workspace root.
//
// usage: pyproject edit-scope <file> [--member M]... [--stale-member M]... [--source S]...
// [--dependency D]... [--global-client true|false] [--path-sources]
func runEditScope(data []byte, args []string, script bool, write func([]byte) error) error {
	flags := flag.NewFlagSet("edit-scope", flag.ContinueOnError)
	flags.SetOutput(os.Stderr)
	edit := scopeEdit{Script: script}
	flags.Var((*repeated)(&edit.Members), "member", "a workspace member the SDK generates")
	flags.Var((*repeated)(&edit.Stale), "stale-member", "a workspace member the SDK generated before and removes now")
	flags.Var((*repeated)(&edit.Sources), "source", "a distribution that resolves to a workspace member")
	flags.Var((*repeated)(&edit.Dependencies), "dependency", "a generated distribution the project depends on")
	globalClient := flags.String("global-client", "", "write (true) or clear (false) the global client flag")
	pathSources := flags.Bool("path-sources", false, "write each source as S=DIR, a path source, and no workspace")
	if err := flags.Parse(args); err != nil {
		return err
	}
	if flags.NArg() > 0 {
		return fmt.Errorf("edit-scope: unexpected argument: %s", flags.Arg(0))
	}
	if *pathSources {
		edit.Paths = map[string]string{}
		for i, source := range edit.Sources {
			name, dir, ok := strings.Cut(source, "=")
			if !ok || name == "" || dir == "" {
				return fmt.Errorf("edit-scope: --path-sources takes each source as NAME=DIR, not %q", source)
			}
			edit.Sources[i] = name
			edit.Paths[normalizeDistribution(name)] = dir
		}
	}
	switch *globalClient {
	case "":
	case "true", "false":
		on := *globalClient == "true"
		edit.GlobalClient = &on
	default:
		return fmt.Errorf("edit-scope: --global-client takes true or false, not %q", *globalClient)
	}
	out, err := editScope(data, edit)
	if err != nil {
		return err
	}
	return write(out)
}

// runSetGlobalClient writes or clears the global client flag and nothing
// else, so clearing it gives back the file as it was before it was set.
//
// usage: pyproject set-global-client <file> true|false
func runSetGlobalClient(data []byte, args []string, write func([]byte) error) error {
	v, err := value(args)
	if err != nil {
		return err
	}
	if v != "true" && v != "false" {
		return fmt.Errorf("set-global-client takes true or false, not %q", v)
	}
	out, err := editGlobalClient(data, v == "true")
	if err != nil {
		return err
	}
	return write(out)
}

// repeated collects every value of a flag given more than once.
type repeated []string

func (r *repeated) String() string { return strings.Join(*r, ",") }

func (r *repeated) Set(v string) error {
	*r = append(*r, v)
	return nil
}

// runRemoveTable cuts one table out of a file, and leaves the file alone
// when it has no such table.
//
// usage: pyproject remove-table <file> <table>
func runRemoveTable(data []byte, args []string, write func([]byte) error) error {
	name, err := value(args)
	if err != nil {
		return err
	}
	out, err := removeTable(data, name)
	if err != nil {
		return err
	}
	return write(out)
}

func value(args []string) (string, error) {
	if len(args) < 3 {
		return "", fmt.Errorf("%s requires a value", args[0])
	}
	return args[2], nil
}

func boolStr(b bool) string {
	if b {
		return "true"
	}
	return "false"
}
