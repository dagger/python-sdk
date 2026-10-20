package main

import (
	"errors"
	"fmt"
	"io/fs"
	"os"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
)

// pythonFloor is the oldest Python the SDK runs on. A test holds it to the
// requires-python of sdk/pyproject.toml.
const pythonFloor = "3.12"

// lastReleaseBelowPythonFloor is the last release of this SDK, whose floor
// was lastReleasePythonFloor. A refusal offers it only to a Python that
// release runs on, as engineRefusal in python-sdk.dang does for engines.
const (
	lastReleaseBelowPythonFloor = "v1.2.0"
	lastReleasePythonFloor      = "3.10"
)

var pythonVersion = regexp.MustCompile(`^(\d+)(?:\.(\d+)(?:\.(\d+))?)?(a|b|rc|\.?dev)?`)

// below reports whether v names a Python older than floor: an older major or
// minor, or a prerelease of the floor's first release. A version it cannot
// read is not below, so an unexpected format never blocks.
func below(v, floor string) bool {
	m := pythonVersion.FindStringSubmatch(v)
	if m == nil {
		return false
	}
	f := pythonVersion.FindStringSubmatch(floor)
	major, floorMajor := atoi(m[1]), atoi(f[1])
	if major != floorMajor || m[2] == "" {
		return major < floorMajor
	}
	minor, floorMinor := atoi(m[2]), atoi(f[2])
	if minor != floorMinor {
		return minor < floorMinor
	}
	return m[4] != "" && (m[3] == "" || m[3] == "0")
}

func atoi(s string) int {
	n, _ := strconv.Atoi(s)
	return n
}

// pythonRefusal says why the module in dir cannot run this SDK, or returns ""
// when it can. uv locks the module to the SDK's floor, but the runtime builds
// it on the Python the module asks for, where the install then fails.
//
// The Python is read the way baseImageFor and pythonVersionFor in
// runtime/build.dang pick the base image; keep them in step.
func pythonRefusal(dir string) (string, error) {
	doc, err := moduleProject(dir)
	if err != nil {
		return "", err
	}
	if getBaseImage(doc) != "" {
		return "", nil
	}

	pin, err := os.ReadFile(filepath.Join(dir, ".python-version"))
	if err == nil {
		v := strings.TrimSpace(string(pin))
		if !below(v, pythonFloor) {
			return "", nil
		}
		return fmt.Sprintf("this Python SDK needs Python %s or later, and .python-version pins Python %s. %s", pythonFloor, v, remedy("Set it to Python "+pythonFloor+" or later", v)), nil
	}
	if !errors.Is(err, fs.ErrNotExist) {
		return "", err
	}

	rp, _ := table(doc, "project")["requires-python"].(string)
	rp = strings.TrimSpace(rp)
	for _, op := range []string{">=", "=="} {
		v, ok := strings.CutPrefix(rp, op)
		lowest, _, _ := strings.Cut(v, ",")
		lowest = strings.TrimSpace(lowest)
		if ok && below(lowest, pythonFloor) {
			return fmt.Sprintf("this Python SDK needs Python %s or later, and requires-python = %q asks for Python %s. %s", pythonFloor, rp, lowest, remedy("Ask for Python "+pythonFloor+" or later", lowest)), nil
		}
	}
	return "", nil
}

// moduleProject reads a module's pyproject.toml, or where it has none, the
// script block of its main.py.
func moduleProject(dir string) (map[string]any, error) {
	data, err := os.ReadFile(filepath.Join(dir, "pyproject.toml"))
	if err == nil {
		return load(data)
	}
	if !errors.Is(err, fs.ErrNotExist) {
		return nil, err
	}
	script := filepath.Join(dir, "main.py")
	data, err = os.ReadFile(script)
	if errors.Is(err, fs.ErrNotExist) {
		return map[string]any{}, nil
	}
	if err != nil {
		return nil, err
	}
	block, err := findScriptBlock(script, data)
	if err != nil || block == nil {
		return map[string]any{}, err
	}
	doc, err := load(block.toml())
	if err != nil {
		return nil, err
	}
	return asProject(doc), nil
}

func remedy(fix, v string) string {
	if below(v, lastReleasePythonFloor) {
		return fix
	}
	return fix + ", or pin the last release of this SDK that runs on it: dagger module install github.com/dagger/python-sdk@" + lastReleaseBelowPythonFloor
}
