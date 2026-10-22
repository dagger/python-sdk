package naming

// This package is a verbatim copy of github.com/dagger/dagger's engine/naming
// (dictionary.go, format.go, naming.go, version.go and testdata/vectors.json),
// at the commit below. Only this file and vectors_test.go are this
// repository's own.
//
// To bump it, copy those files again from a newer commit, set Commit, run
// `go mod tidy` and `go test ./...` in helpers/names, and raise
// CoveredVersion in main.go to the engine release line that commit belongs
// to. TestVectors fails when the copy no longer formats the engine's own test
// vectors as the engine does.
const (
	Source = "https://github.com/dagger/dagger/tree/" + Commit + "/engine/naming"
	Commit = "198d001192d3179a8215980e97c766026f2dd0c2"
)
