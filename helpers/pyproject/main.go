package main

import (
	"fmt"
	"os"
)

func main() {
	if err := run(os.Args[1:]); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}

// run dispatches a subcommand. get-* commands print to stdout (no trailing
// newline). set-*/unset-* commands edit the file in place.
//
// usage: pyproject <command> <file> [value]
func run(args []string) error {
	if len(args) < 2 {
		return fmt.Errorf("usage: pyproject <command> <file> [value]")
	}
	cmd, path := args[0], args[1]

	data, err := os.ReadFile(path)
	if err != nil {
		return err
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
		fmt.Print(boolStr(getUseUv(doc)))
		return nil
	case "get-base-image":
		fmt.Print(getBaseImage(doc))
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
	case "unset-base-image":
		unsetBaseImage(doc)
	default:
		return fmt.Errorf("unknown command: %s", cmd)
	}

	out, err := dump(doc)
	if err != nil {
		return err
	}
	return os.WriteFile(path, out, 0o644)
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
