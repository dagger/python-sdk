package main

import (
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
// usage: pyproject <command> <file> [value | flags]
func run(args []string) error {
	if len(args) < 2 {
		return fmt.Errorf("usage: pyproject <command> <file> [value | flags]")
	}
	cmd, path := args[0], args[1]

	data, err := os.ReadFile(path)
	if err != nil {
		return err
	}
	switch cmd {
	case "edit-scope":
		return runEditScope(path, data, args[2:])
	case "set-global-client":
		return runSetGlobalClient(path, data, args)
	}
	doc, err := load(data)
	if err != nil {
		return err
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

	out, err := dump(doc)
	if err != nil {
		return err
	}
	return os.WriteFile(path, out, 0o644)
}

// runEditScope sets the SDK-owned entries of a scope pyproject.toml, and
// leaves the file alone when nothing changes.
//
// usage: pyproject edit-scope <file> [--member M]... [--source S]...
// [--dependency D]... [--global-client true|false]
func runEditScope(path string, data []byte, args []string) error {
	flags := flag.NewFlagSet("edit-scope", flag.ContinueOnError)
	flags.SetOutput(os.Stderr)
	var edit scopeEdit
	flags.Var((*repeated)(&edit.Members), "member", "a workspace member the SDK generates")
	flags.Var((*repeated)(&edit.Sources), "source", "a distribution that resolves to a workspace member")
	flags.Var((*repeated)(&edit.Dependencies), "dependency", "a generated distribution the project depends on")
	globalClient := flags.String("global-client", "", "write (true) or clear (false) the global client flag")
	if err := flags.Parse(args); err != nil {
		return err
	}
	if flags.NArg() > 0 {
		return fmt.Errorf("edit-scope: unexpected argument: %s", flags.Arg(0))
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
	if string(out) == string(data) {
		return nil
	}
	return os.WriteFile(path, out, 0o644)
}

// runSetGlobalClient writes or clears the global client flag and nothing
// else, so clearing it gives back the file as it was before it was set.
//
// usage: pyproject set-global-client <file> true|false
func runSetGlobalClient(path string, data []byte, args []string) error {
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
	if string(out) == string(data) {
		return nil
	}
	return os.WriteFile(path, out, 0o644)
}

// repeated collects every value of a flag given more than once.
type repeated []string

func (r *repeated) String() string { return strings.Join(*r, ",") }

func (r *repeated) Set(v string) error {
	*r = append(*r, v)
	return nil
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
