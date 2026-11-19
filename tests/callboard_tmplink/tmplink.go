// Package tmplink reaches the tests' temp folders through a symlink (a MyStandards template: tmplink; callboard copies
// it to tests/callboard_tmplink/). Each package's tests start with it (imported as <module>/tests/callboard_tmplink):
//
//	func TestMain(m *testing.M) { tmplink.Main(m) }
//
// The temp folder (t.TempDir, os.MkdirTemp, a program's TMPDIR) is then reached through a symlink on every system, as
// a Mac's own is (/var is /private/var): a path said as given where one as it really is was meant (git gives a repo's
// top as it really is) fails there, on Linux and GitHub too, and not only on a Mac. It's removed when the tests end.
// A copy of the template, kept in step with it: never edit it here.
package tmplink

import (
	"fmt"
	"os"
	"path/filepath"
	"testing"
)

// Main runs the package's tests with their temp folders reached through a symlink, then exits with their result.
func Main(m *testing.M) {
	base, err := os.MkdirTemp("", "tmplink-")
	if err != nil {
		panic(fmt.Sprintf("tmplink: %v", err))
	}
	actual, link := filepath.Join(base, "real"), filepath.Join(base, "link")
	if err := os.Mkdir(actual, 0o755); err != nil {
		panic(fmt.Sprintf("tmplink: %v", err))
	}
	if err := os.Symlink(actual, link); err != nil {
		panic(fmt.Sprintf("tmplink: the tests' temp folder can't be reached through a symlink here: %v", err))
	}
	for _, k := range []string{"TMPDIR", "TMP", "TEMP"} {
		os.Setenv(k, link)
	}
	code := m.Run()
	os.RemoveAll(base)
	os.Exit(code)
}
