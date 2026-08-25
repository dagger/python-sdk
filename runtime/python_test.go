package main

import (
	"testing"

	"github.com/stretchr/testify/require"
)

func TestProjectNameNormalization(t *testing.T) {
	// Valid "dagger.json" names
	inputs := []string{
		"friendly-bard",
		"Friendly-Bard",
		"FRIENDLY-BARD",
		"friendly.bard",
		"friendly_bard",
		"friendly--bard",
		"friendly-.bard",
		"Friendly-..-bard",
		"friendly--bard",
		"_friendly . bard_",
		"--friendly_bard--",
		" friendly_bard ",
		"friendly bard",
		"Friendly Bard",
		"friendlyBard",
	}
	for _, input := range inputs {
		// require.Equal(t, "friendly-bard",  NormalizeProjectName(input)
		require.Equalf(t, "friendly-bard", NormalizeProjectNameFromModule(input), "input: %s", input)
	}
	require.Equal(t, "friendly-2", NormalizeProjectNameFromModule("friendly2"))
}

func TestPackageNameNormalization(t *testing.T) {
	// NormalizePackageName documents that it takes an already-normalized
	// project name, so that is what it gets here; the messier module names it
	// used to be handed directly are covered by TestProjectNameNormalization,
	// which is the step that actually normalizes them.
	require.Equal(t, "friendly_bard", NormalizePackageName("friendly-bard"))
	require.Equal(t, "friendly_2", NormalizePackageName("friendly-2"))

	// The two steps compose: any module name reaches the same package name,
	// which is the path discovery takes.
	for _, input := range []string{
		"friendly-bard",
		"Friendly-Bard",
		"FRIENDLY-BARD",
		"friendly.bard",
		"friendly_bard",
		"friendly--bard",
		"friendlyBard",
	} {
		got := NormalizePackageName(NormalizeProjectNameFromModule(input))
		require.Equalf(t, "friendly_bard", got, "input: %s", input)
	}
}
