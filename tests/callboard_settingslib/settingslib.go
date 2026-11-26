// Package settingslib is the settings format's reader for Go (a shared library from MyStandards: settingslib; the
// format is MyStandards' SETTINGS.md). It is settingslib.py's twin: the same file read the same way, held to the same
// format with the same words, and the same answers for a setting by its id.
//
// A project says every setting it offers in one file (settings.toml): what each is, the field it gets, its default,
// where its value is saved, and its info — what it does, when to use it and its configuration, which every setting
// must have. This reads that file, holds it to the format, and answers for any setting by its id: its definition, its
// value, its default as it stands, where it's saved. A value is read and written only here, by the setting's id, so
// none is saved that has no definition (and so no info). The page's drawer (settings.js, beside this) draws a setting
// from what Page gives it.
//
//	cat, err := settingslib.Load("settings.toml")    the file, held to the format (an error: everything wrong with it)
//	cat.Store("project", settingslib.NewTomlStore(path, "", nil))   where the values of the file a setting calls "project" are kept
//	cat.Answer("manifest_domain", fn)                what the app works out: a default_from, a choices_from, a set's
//	                                                 members_from, a shown_when's feature — fn(member) answers it
//	cat.Add(settings, nil, nil)                      settings that are themselves data, added to the file's
//	cat.ValueOf("server_url", "nas1")                the value that holds: saved, else the set's shared one, else the default
//	cat.SavedValue(id, member...)                    only what's saved (nil: nothing)
//	cat.DefaultOf(id, member...)                     {Value, Says, How: "fixed" | "worked_out" | "required" | "shared" | "cascade"}
//	cat.SaveValue(id, value, member...)              held to the setting (its kind, type, choices, limits) and written;
//	                                                 nil takes it away (back to its default)
//	cat.Page(members)                                everything the drawer needs, as JSON
//	cat.Unknown("project", tables)                   saved values at a key no setting has
//	settingslib.Problems(data)                       what's wrong with a settings file's content: [{Check, Line}]
//
// A set's member is its id ("nas1"); a setting in two sets takes both, outer first ("unit", "push"); "*" is a shared
// set's value for every member that has none of its own. A copy of the template, kept in step with it: never edit it
// here.
package settingslib

import (
	"bytes"
	_ "embed"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"reflect"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"unicode"
	"unicode/utf8"

	toml "github.com/pelletier/go-toml/v2"
	"github.com/pelletier/go-toml/v2/unstable"
)

// Format is the version of the format this reads.
const Format = 1

// Shared is a shared set's member for "every one that has none of its own".
const Shared = "*"

// Drawer is the settings drawer's script (settings.js, beside this, the same copy as every app's): what a Go app's
// page runs to draw its settings from what Page gives.
//
//go:embed settings.js
var Drawer string

// Kinds are the fields a setting may get: a closed list.
var Kinds = []string{"switch", "choice", "number", "slider", "text", "lines", "list", "pairs", "secret"}

// Types are what a text is held to, by its type's name: the same in every app.
var Types = map[string]string{
	"host": `[A-Za-z0-9\[][A-Za-z0-9._:\[\]-]*`,
	"port": `[0-9]{1,5}`,
	"path": `/[^\x00-\x1f]*`,
	"url":  `https?://[^\s\x00-\x1f]+`,
	"name": `[A-Za-z0-9_][A-Za-z0-9_-]*`,
	"glob": `[^\x00-\x1f]+`,
}

var typeOrder = []string{"host", "port", "path", "url", "name", "glob"}

// Checks are the checks Problems makes (orphans and runtime_copy are the project's own).
var Checks = []string{"format", "info", "defaults", "subsettings", "fields", "unique"}

var (
	idRe      = regexp.MustCompile(`^[a-z][a-z0-9_]*$`)
	keyPartRe = regexp.MustCompile(`^[A-Za-z0-9_-]+$`)
	wordRe    = regexp.MustCompile(`[a-z0-9][a-z0-9'-]*`)
	defaultRe = regexp.MustCompile(`(?i)\bdefaults?\b`)
	controlRe = regexp.MustCompile(`[\x00-\x1f\x7f]`)
	typeRes   = map[string]*regexp.Regexp{}
)

func init() {
	for name, p := range Types {
		typeRes[name] = regexp.MustCompile(`^(?:` + p + `)$`)
	}
}

// The kind of value each field of a setting takes ("any": any value).
var fields = map[string]string{
	"label": "str", "summary": "str", "info": "dict", "kind": "str", "group": "str", "saved": "dict", "under": "str",
	"choices": "list", "choices_from": "str", "min": "num", "max": "num", "step": "num", "unit": "str", "type": "str",
	"pattern": "str", "pattern_says": "str", "default": "any", "default_from": "str", "default_says": "str",
	"required": "bool", "revert": "bool", "revert_why": "str", "shown_when": "dict", "icon": "str", "each": "strlist",
}

type fieldList struct {
	names []string
	kinds map[string]string
}

var groupFields = fieldList{[]string{"title", "page", "info"}, map[string]string{"title": "str", "page": "str", "info": "str"}}
var setFields = fieldList{[]string{"title", "member", "saved", "members_from", "shared", "shared_saved", "cascade", "list"},
	map[string]string{"title": "str", "member": "str", "saved": "dict", "members_from": "str", "shared": "bool", "shared_saved": "dict", "cascade": "bool", "list": "bool"}}

// pairName is a name in a pairs setting: an environment variable's, a header's.
var pairName = regexp.MustCompile(`^[A-Za-z0-9_][A-Za-z0-9_.-]*$`)

// At begins a list set's member in a path: its place in the list, from 1, as "#1".
const At = "#"

// A setting's info, in three parts: what the setting does, when one would use it, and its configuration (what to give
// it). Each has the fewest letters it may be said in, and the fewest words of its own: words that aren't already in the
// setting's label and summary, nor (for when and config) in the parts before it.
var (
	infoParts = []string{"does", "when", "config"}
	infoSays  = map[string]string{"does": "what it does", "when": "when to use it", "config": "its configuration: what to give it"}
	infoLeast = map[string]int{"does": 80, "when": 80, "config": 50}
	infoOwn   = map[string]int{"does": 6, "when": 6, "config": 4}
	given     = []string{"choice", "number", "slider", "text", "lines", "list", "pairs", "secret"}
	small     = map[string]bool{}
)

func init() {
	for _, w := range strings.Fields("a an the of to in on is are it its it's that this and or for with as by be at from not no than each every which what when where how " +
		"they them their there here you your one can may will if so then into out up do does only also") {
		small[w] = true
	}
}

// Error is a settings file that isn't in the format, a setting that isn't there, or a value a setting can't take.
type Error struct{ Msg string }

func (e *Error) Error() string { return e.Msg }

func fail(format string, a ...any) error { return &Error{fmt.Sprintf(format, a...)} }

// Problem is one thing wrong with a settings file: the check it fails, and the line that says so.
type Problem struct {
	Check, Line string
}

// Order is the order a file says its groups, sets and settings in: {"settings": [ids]…}.
type Order map[string][]string

// ---------------------------------------------------------------- values, as Python sees them

func isMap(v any) (map[string]any, bool) {
	m, ok := v.(map[string]any)
	return m, ok
}

// asList is a list of any kind as []any (false: not a list).
func asList(v any) ([]any, bool) {
	if l, ok := v.([]any); ok {
		return l, true
	}
	if v == nil {
		return nil, false
	}
	r := reflect.ValueOf(v)
	if r.Kind() != reflect.Slice && r.Kind() != reflect.Array {
		return nil, false
	}
	out := make([]any, r.Len())
	for i := range out {
		out[i] = r.Index(i).Interface()
	}
	return out, true
}

// asNum is a number's value (false: not a number; a bool isn't one).
func asNum(v any) (float64, bool) {
	switch n := v.(type) {
	case int:
		return float64(n), true
	case int8:
		return float64(n), true
	case int16:
		return float64(n), true
	case int32:
		return float64(n), true
	case int64:
		return float64(n), true
	case uint:
		return float64(n), true
	case uint8:
		return float64(n), true
	case uint16:
		return float64(n), true
	case uint32:
		return float64(n), true
	case uint64:
		return float64(n), true
	case float32:
		return float64(n), true
	case float64:
		return n, true
	}
	return 0, false
}

func isStr(v any) bool { _, ok := v.(string); return ok }

func hasKey(m map[string]any, k string) bool { _, ok := m[k]; return ok }

// isInt says whether v is a whole number of an integer type (as Python's int is: not a float, not a bool).
func isInt(v any) (int64, bool) {
	r := reflect.ValueOf(v)
	switch r.Kind() {
	case reflect.Int, reflect.Int8, reflect.Int16, reflect.Int32, reflect.Int64:
		return r.Int(), true
	case reflect.Uint, reflect.Uint8, reflect.Uint16, reflect.Uint32, reflect.Uint64:
		return int64(r.Uint()), true
	}
	return 0, false
}

// truthy is Python's bool(v).
func truthy(v any) bool {
	if v == nil {
		return false
	}
	if b, ok := v.(bool); ok {
		return b
	}
	if n, ok := asNum(v); ok {
		return n != 0
	}
	if s, ok := v.(string); ok {
		return s != ""
	}
	if m, ok := isMap(v); ok {
		return len(m) > 0
	}
	if l, ok := asList(v); ok {
		return len(l) > 0
	}
	return true
}

// same is Python's ==, for the values a setting holds (1 == 1.0; lists and tables by their contents).
func same(a, b any) bool {
	if x, ok := asNum(a); ok {
		y, ok := asNum(b)
		return ok && x == y
	}
	if _, ok := asNum(b); ok {
		return false
	}
	if la, ok := asList(a); ok {
		lb, ok := asList(b)
		if !ok || len(la) != len(lb) {
			return false
		}
		for i := range la {
			if !same(la[i], lb[i]) {
				return false
			}
		}
		return true
	}
	if ma, ok := isMap(a); ok {
		mb, ok := isMap(b)
		if !ok || len(ma) != len(mb) {
			return false
		}
		for k, v := range ma {
			w, ok := mb[k]
			if !ok || !same(v, w) {
				return false
			}
		}
		return true
	}
	return reflect.DeepEqual(a, b)
}

// pyStr is Python's str(v), for the values said in a message.
func pyStr(v any) string {
	switch x := v.(type) {
	case nil:
		return "None"
	case string:
		return x
	case bool:
		if x {
			return "True"
		}
		return "False"
	case float32, float64:
		f, _ := asNum(x)
		if f == float64(int64(f)) && f < 1e16 && f > -1e16 {
			return strconv.FormatInt(int64(f), 10) + ".0"
		}
		return strconv.FormatFloat(f, 'g', -1, 64)
	}
	if n, ok := asNum(v); ok {
		return strconv.FormatFloat(n, 'f', -1, 64)
	}
	if l, ok := asList(v); ok {
		parts := make([]string, len(l))
		for i, x := range l {
			parts[i] = pyRepr(x)
		}
		return "[" + strings.Join(parts, ", ") + "]"
	}
	return fmt.Sprint(v)
}

// pyRepr is Python's repr(v), for the values said in a message.
func pyRepr(v any) string {
	if s, ok := v.(string); ok {
		return "'" + s + "'"
	}
	return pyStr(v)
}

func strs(l []string) []any {
	out := make([]any, len(l))
	for i, s := range l {
		out[i] = s
	}
	return out
}

func has(list []string, s string) bool {
	for _, x := range list {
		if x == s {
			return true
		}
	}
	return false
}

func sortedKeys[V any](m map[string]V) []string {
	out := make([]string, 0, len(m))
	for k := range m {
		out = append(out, k)
	}
	sort.Strings(out)
	return out
}

// ordered is m's keys in the order said (those it doesn't say: sorted, after them).
func ordered[V any](m map[string]V, said []string) []string {
	out, seen := []string{}, map[string]bool{}
	for _, k := range said {
		if _, ok := m[k]; ok && !seen[k] {
			out, seen[k] = append(out, k), true
		}
	}
	for _, k := range sortedKeys(m) {
		if !seen[k] {
			out = append(out, k)
		}
	}
	return out
}

func typeOK(kind string, v any) bool {
	switch kind {
	case "any":
		return true
	case "str":
		return isStr(v)
	case "dict":
		_, ok := isMap(v)
		return ok
	case "list":
		_, ok := asList(v)
		return ok && !isStr(v)
	case "num":
		_, ok := asNum(v)
		return ok
	case "bool":
		_, ok := v.(bool)
		return ok
	case "strlist":
		_, ok := asList(v)
		return isStr(v) || ok
	}
	return false
}

func each(s map[string]any) []string {
	switch e := s["each"].(type) {
	case nil:
		return nil
	case string:
		return []string{e}
	}
	l, _ := asList(s["each"])
	out := make([]string, len(l))
	for i, x := range l {
		out[i] = pyStr(x)
	}
	return out
}

func words(text string) []string {
	var out []string
	for _, w := range wordRe.FindAllString(strings.ToLower(text), -1) {
		if small[w] {
			continue
		}
		if utf8.RuneCountInString(w) > 3 && strings.HasSuffix(w, "s") && !strings.HasSuffix(w, "ss") {
			w = w[:len(w)-1]
		}
		out = append(out, w)
	}
	return out
}

func sentences(text string) bool {
	t := []rune(strings.TrimSpace(text))
	return len(t) > 1 && (unicode.IsUpper(t[0]) || !unicode.IsLetter(t[0])) && strings.ContainsRune(".!?)", t[len(t)-1])
}

// InfoProblems is what's wrong with a setting's info (nil for nothing): each part it must have (does and when; config
// for a kind a value is given to — a sub-setting's when may be left out: its parent says when), each at least its
// letters, a sentence or more, and with words of its own.
func InfoProblems(s map[string]any, sub bool) []string {
	info, ok := isMap(s["info"])
	if !ok || len(info) == 0 {
		return []string{"info: missing (every setting says what it does, when to use it, and its configuration: [settings.<id>.info] does, when, config)"}
	}
	var bad []string
	for _, k := range sortedKeys(info) {
		if !has(infoParts, k) {
			bad = append(bad, fmt.Sprintf("info.%s: not a part of it (%s)", k, strings.Join(infoParts, ", ")))
		}
	}
	told := map[string]bool{}
	for _, w := range words(pyStr(orEmpty(s, "label")) + " " + pyStr(orEmpty(s, "summary"))) {
		told[w] = true
	}
	for _, part := range infoParts {
		v, there := info[part]
		kind, _ := s["kind"].(string)
		must := part == "does" || (part == "when" && !sub) || (part == "config" && has(given, kind))
		if !there {
			if must {
				bad = append(bad, fmt.Sprintf("info.%s: missing (%s)", part, infoSays[part]))
			}
			continue
		}
		text, ok := v.(string)
		if !ok || strings.TrimSpace(text) == "" {
			bad = append(bad, fmt.Sprintf("info.%s: %s, in words", part, infoSays[part]))
			continue
		}
		text = strings.TrimSpace(text)
		if n := utf8.RuneCountInString(text); n < infoLeast[part] {
			bad = append(bad, fmt.Sprintf("info.%s: %d letters, and %s takes %d at least to say", part, n, infoSays[part], infoLeast[part]))
		}
		if !sentences(text) {
			bad = append(bad, fmt.Sprintf("info.%s: one or more sentences (a capital first, a full stop last)", part))
		}
		own := map[string]bool{}
		for _, w := range words(text) {
			if !told[w] {
				own[w] = true
			}
		}
		if len(own) < infoOwn[part] {
			before := "its label, its summary and the parts before it"
			if part == "does" {
				before = "its label and summary"
			}
			bad = append(bad, fmt.Sprintf("info.%s: says %s again (%d words of its own, and it takes %d): %s, which they don't say", part, before, len(own), infoOwn[part], infoSays[part]))
		}
		for _, w := range words(text) {
			told[w] = true
		}
	}
	return bad
}

func orEmpty(m map[string]any, k string) any {
	if v, ok := m[k]; ok {
		return v
	}
	return ""
}

// Problems is what's wrong with a settings file as read: (the check it fails, the line that says so) for each, nil
// where it's in the format.
func Problems(data map[string]any) []Problem { return problems(data, false, nil) }

func problems(data map[string]any, extra bool, ord Order) []Problem {
	var bad []Problem
	say := func(check, format string, a ...any) { bad = append(bad, Problem{check, fmt.Sprintf(format, a...)}) }
	app := ""
	f, fok := isMap(data["format"])
	if !extra {
		v, isNum := asNum(f["version"])
		if _, isBool := f["version"].(bool); !fok || !isNum || isBool || v != Format {
			say("format", "[format] version = %d: missing (the format this file is in)", Format)
		} else {
			app, _ = f["app"].(string)
			for _, k := range sortedKeys(f) {
				if k != "version" && k != "app" {
					say("format", "[format] %s: not a key of it (version, app)", k)
				}
			}
		}
	} else if fok {
		app, _ = f["app"].(string)
	}
	for _, k := range sortedKeys(data) {
		if !has([]string{"format", "groups", "sets", "settings"}, k) {
			say("format", "[%s]: not a table of a settings file (format, groups, sets, settings)", k)
		}
	}
	table := func(k string) map[string]any { m, _ := isMap(data[k]); return m }
	groups, sets, settings := table("groups"), table("sets"), table("settings")
	for _, t := range []struct {
		name  string
		table map[string]any
		f     fieldList
		must  []string
	}{{"groups", groups, groupFields, []string{"title"}}, {"sets", sets, setFields, []string{"title", "member", "saved", "members_from"}}} {
		for _, id := range ordered(t.table, ord[t.name]) {
			where := fmt.Sprintf("[%s.%s]", t.name, id)
			if !idRe.MatchString(id) {
				say("format", "%s: an id is lowercase letters, digits and _", where)
			}
			g, ok := isMap(t.table[id])
			if !ok {
				say("format", "%s: a table", where)
				continue
			}
			for _, k := range sortedKeys(g) {
				if kind, known := t.f.kinds[k]; !known || !typeOK(kind, g[k]) {
					say("format", "%s %s: not a key of it (%s), or not the kind of value it takes", where, k, strings.Join(t.f.names, ", "))
				}
			}
			for _, k := range t.must {
				if !truthy(g[k]) {
					say("format", "%s %s: missing", where, k)
				}
			}
			if t.name != "sets" {
				continue
			}
			if saved, ok := isMap(g["saved"]); ok {
				savedProblems(saved, where, say, false, true)
			}
			if g["shared"] == true && g["cascade"] == true {
				say("format", "%s: a set is shared (one value for every member) or cascade (each member takes the one before it's), not both", where)
			}
			if g["list"] == true { // (its members are the items of a list, [[key]]: each by its place in it)
				if g["shared"] == true {
					say("format", "%s: a list's members are its items, and there's no value for every one of them (shared)", where)
				}
				if saved, _ := isMap(g["saved"]); !truthy(saved["key"]) {
					say("format", "%s saved.key: where its list is kept", where)
				}
			}
			if ss, there := g["shared_saved"]; there {
				if g["shared"] != true {
					say("format", "%s shared_saved: only a set that's shared has a value for every member (shared = true)", where)
				}
				if m, ok := isMap(ss); ok {
					savedProblems(m, where+" shared_saved:", say, false, true)
				}
			}
		}
	}
	seen := map[string][]string{}
	for _, id := range ordered(settings, ord["settings"]) {
		where := fmt.Sprintf("[settings.%s]", id)
		if !idRe.MatchString(id) {
			say("format", "%s: an id is lowercase letters, digits and _", where)
		}
		s, ok := isMap(settings[id])
		if !ok {
			say("format", "%s: a table", where)
			continue
		}
		for _, k := range sortedKeys(s) {
			own := app != "" && strings.HasPrefix(k, app+"_")
			if k == "info" || own {
				continue // (its parts are held under "info", below; a field of the app's own is the app's)
			}
			if kind, known := fields[k]; !known || !typeOK(kind, s[k]) {
				mine := ""
				if app != "" {
					mine = fmt.Sprintf(" (one of %s's own is named %s_<field>)", app, app)
				}
				say("format", "%s %s: not a field of a setting%s, or not the kind of value it takes", where, k, mine)
			}
		}
		var parent map[string]any
		parentOK := false
		if truthy(s["under"]) {
			under, _ := s["under"].(string)
			parent, parentOK = isMap(settings[under])
		}
		for _, k := range []string{"label", "kind", "saved"} {
			if !truthy(s[k]) {
				say("format", "%s %s: missing", where, k)
			}
		}
		kind, _ := s["kind"].(string)
		if truthy(s["kind"]) && !has(Kinds, kind) {
			say("format", "%s kind = %s: one of %s", where, pyRepr(s["kind"]), strings.Join(Kinds, ", "))
		}
		group := s["group"]
		if !truthy(group) {
			group = ""
			if parentOK {
				group = orEmpty(parent, "group")
			}
		}
		if !truthy(group) {
			say("format", "%s group: missing", where)
		} else if g, named := group.(string); !named || !hasKey(groups, g) {
			say("format", "%s group = %s: no such group", where, pyRepr(group))
		}
		es := each(s)
		for _, e := range es {
			if !hasKey(sets, e) {
				say("format", "%s each = %s: no such set", where, pyRepr(e))
			}
		}
		if len(es) > 2 {
			say("format", "%s each: two sets at the most, one inside the other", where)
		}
		if saved, ok := isMap(s["saved"]); ok {
			savedProblems(saved, where, say, len(es) > 0, false)
			if len(es) > 0 && !truthy(saved["file"]) {
				setSays := false
				for _, e := range es {
					if set, ok := isMap(sets[e]); ok {
						if ss, ok := isMap(set["saved"]); ok && truthy(ss["file"]) {
							setSays = true
						}
					}
				}
				if !setSays {
					say("format", "%s saved.file: neither it nor its set says which of the app's files", where)
				}
			}
		}
		if when, ok := isMap(s["shown_when"]); ok {
			setting, _ := when["setting"].(string)
			_, isSetting := settings[setting]
			feature, isFeature := when["feature"].(string)
			_, hasIs := when["is"]
			okSetting := len(when) == 2 && hasIs && isStr(when["setting"]) && isSetting
			okFeature := len(when) == 1 && isFeature && feature != ""
			if !okSetting && !okFeature {
				say("format", "%s shown_when: { setting = \"<a setting's id>\", is = <its value> }, or { feature = \"<what the app answers>\" }", where)
			}
		}
		// info: required, in its parts, each long enough and with words of its own
		_, sub := s["under"]
		for _, line := range InfoProblems(s, sub) {
			say("info", "%s %s", where, line)
		}
		if summary, ok := s["summary"].(string); ok && defaultRe.MatchString(summary) {
			say("info", "%s summary: says the default (the revert button does that)", where)
		}
		// the default: said exactly one way
		var ways []string
		for _, k := range []string{"default", "default_from"} {
			if _, ok := s[k]; ok {
				ways = append(ways, k)
			}
		}
		if s["required"] == true {
			ways = append(ways, "required")
		}
		if len(ways) != 1 {
			how := "no way"
			if len(ways) > 1 {
				how = "more than one way (" + strings.Join(ways, ", ") + ")"
			}
			say("defaults", "%s: says its default %s: default (a fixed value), default_from with default_says (worked out), or required = true (there's none)", where, how)
		}
		_, from := s["default_from"]
		says, hasSays := s["default_says"]
		if from != hasSays || (hasSays && !sentences(pyStr(says)+".")) {
			say("defaults", "%s: default_from (what works it out) and default_says (how, in words) go together", where)
		}
		if s["revert"] == false && strings.TrimSpace(pyStr(orEmpty(s, "revert_why"))) == "" {
			say("defaults", "%s revert = false: says why (revert_why)", where)
		}
		// a sub-setting
		if under, there := s["under"]; there {
			if !parentOK {
				say("subsettings", "%s under = %s: no such setting", where, pyRepr(under))
			} else {
				if up, ok := parent["under"]; ok {
					say("subsettings", "%s under = %s: that's under %s itself (one level only)", where, pyRepr(under), pyRepr(up))
				}
				if truthy(s["group"]) && !same(s["group"], parent["group"]) {
					say("subsettings", "%s group: its parent's (%s), or left out", where, pyRepr(parent["group"]))
				}
				if !same(strs(es), strs(each(parent))) {
					say("subsettings", "%s each: its parent's set (%s)", where, pyStr(strs(each(parent))))
				}
				if _, ok := s["shown_when"]; ok {
					say("subsettings", "%s shown_when: a sub-setting is shown when its parent is", where)
				}
			}
		}
		// the field its kind gets
		_, hasChoices := s["choices"]
		_, hasFrom := s["choices_from"]
		if kind == "choice" && hasChoices == hasFrom {
			say("fields", "%s: a choice has choices or choices_from (one of them)", where)
		}
		choices, pairs := choicePairs(s["choices"])
		if hasChoices && !pairs {
			say("fields", "%s choices: [[value, \"Label\"], …]", where)
		}
		_, hasMin := s["min"]
		_, hasMax := s["max"]
		if (kind == "number" || kind == "slider") && !(hasMin && hasMax) {
			say("fields", "%s: a %s has its min and max", where, kind)
		}
		if (kind == "number" || kind == "slider") && hasMin && hasMax {
			lo, ok1 := asNum(s["min"])
			hi, ok2 := asNum(s["max"])
			if ok1 && ok2 && lo > hi {
				say("fields", "%s: its min is over its max", where)
			}
		}
		if t, there := s["type"]; there {
			name, _ := t.(string)
			if !has([]string{"text", "list", "lines", "pairs"}, kind) || Types[name] == "" {
				say("fields", "%s type = %s: for a text, a list, lines or pairs, one of %s", where, pyRepr(t), strings.Join(typeOrder, ", "))
			}
		}
		if p, there := s["pattern"]; there {
			if strings.TrimSpace(pyStr(orEmpty(s, "pattern_says"))) == "" {
				say("fields", "%s pattern: says what's wanted when it doesn't match (pattern_says)", where)
			}
			if _, err := regexp.Compile(pyStr(p)); err != nil {
				say("fields", "%s pattern: %v", where, err)
			}
		}
		if d, there := s["default"]; there && has(Kinds, kind) && kind != "secret" {
			var cs []any
			if pairs {
				cs = choices
			}
			if _, err := held(id, s, d, cs, pairs); err != nil {
				say("fields", "%s default: %v", where, err)
			}
		}
		if _, there := s["default"]; kind == "secret" && there {
			say("fields", "%s: a secret has no default in the file", where)
		}
		// one id, one place saved
		if saved, ok := isMap(s["saved"]); ok {
			if key, ok := saved["key"].(string); ok {
				at := strings.Join(es, "\x00") + "\x01" + pyStr(orEmpty(saved, "file")) + "\x01" + key
				for _, other := range seen[at] {
					a, _ := isMap(s["shown_when"])
					o, _ := isMap(settings[other])
					b, _ := isMap(o["shown_when"])
					_, aSetting := a["setting"]
					if !(aSetting && same(a["setting"], b["setting"]) && !same(a["is"], b["is"])) {
						say("unique", "%s saved: %s is saved there too (%s)", where, other, key)
					}
				}
				seen[at] = append(seen[at], id)
			}
		}
	}
	for _, k := range []string{"groups", "sets"} {
		if v, there := data[k]; there {
			if _, ok := isMap(v); !ok {
				say("format", "[%s]: a table of them", k)
			}
		}
	}
	return bad
}

// choicePairs is a choice's values, where its choices are [[value, "Label"]…] (false where they aren't).
func choicePairs(v any) ([]any, bool) {
	l, ok := asList(v)
	if !ok || len(l) == 0 || isStr(v) {
		return nil, false
	}
	out := make([]any, len(l))
	for i, c := range l {
		pair, ok := asList(c)
		if !ok || isStr(c) || len(pair) != 2 || !isStr(pair[1]) {
			return nil, false
		}
		out[i] = pair[0]
	}
	return out, true
}

func savedProblems(saved map[string]any, where string, say func(string, string, ...any), inSet, ofSet bool) {
	for _, k := range sortedKeys(saved) {
		if k != "file" && k != "key" {
			say("format", "%s saved.%s: not a key of it (file, key)", where, k)
		}
	}
	key, isKey := saved["key"].(string)
	good := isKey && (key != "" || ofSet)
	if good && key != "" {
		for _, p := range strings.Split(key, ".") {
			if !keyPartRe.MatchString(p) {
				good = false
			}
		}
	}
	if !good {
		say("format", "%s saved.key: the dotted path its value is kept at", where)
	}
	file, there := saved["file"]
	fileOK := isStr(file) && file != ""
	if (inSet || ofSet) && there && !fileOK {
		say("format", "%s saved.file: which of the app's files (project, computer, or a name it gives one)", where)
	}
	if !inSet && !ofSet && !fileOK {
		say("format", "%s saved.file: which of the app's files (project, computer, or a name it gives one)", where)
	}
}

// held is value as the setting keeps it, held to its kind (an Error where it can't be that). checkChoices: whether
// choices (a choice's values) are known, and so held to.
func held(id string, s map[string]any, value any, choices []any, checkChoices bool) (any, error) {
	kind, _ := s["kind"].(string)
	label := pyStr(s["label"])
	if !truthy(s["label"]) {
		label = id
	}
	no := func(why string) error { return &Error{label + ": " + why} }
	text := func(v any) (string, error) {
		t, ok := v.(string)
		look := t
		if kind == "lines" {
			look = strings.ReplaceAll(t, "\n", "")
		}
		if !ok || controlRe.MatchString(look) {
			return "", no("text, with nothing in it a line can't hold")
		}
		typ, _ := s["type"].(string)
		if typ == "port" {
			n, err := strconv.Atoi(t)
			if !typeRes["port"].MatchString(t) || err != nil || n <= 0 || n >= 65536 {
				return "", no("a port: a number from 1 to 65535")
			}
		} else if re := typeRes[typ]; typ != "" && re != nil && !re.MatchString(t) {
			return "", no("doesn't look like a " + typ)
		}
		if p, there := s["pattern"]; there {
			re, err := regexp.Compile(`^(?:` + pyStr(p) + `)$`)
			if err != nil || !re.MatchString(t) {
				why := strings.TrimSpace(pyStr(orEmpty(s, "pattern_says")))
				if !truthy(s["pattern_says"]) {
					why = "isn't what's wanted here"
				}
				return "", no(why)
			}
		}
		return t, nil
	}
	switch kind {
	case "switch":
		if _, ok := value.(bool); !ok {
			return nil, no("on or off (true or false)")
		}
		return value, nil
	case "choice":
		if checkChoices {
			for _, c := range choices {
				if same(c, value) {
					return value, nil
				}
			}
			names := make([]string, len(choices))
			for i, c := range choices {
				names[i] = pyStr(c)
			}
			return nil, no("one of " + strings.Join(names, ", "))
		}
		return value, nil
	case "number", "slider":
		n, ok := asNum(value)
		if !ok {
			return nil, no("a number")
		}
		lo, hasLo := asNum(s["min"])
		hi, hasHi := asNum(s["max"])
		if (hasLo && n < lo) || (hasHi && n > hi) {
			return nil, no(fmt.Sprintf("from %s to %s", pyStr(s["min"]), pyStr(s["max"])))
		}
		return value, nil
	case "pairs": // (names, each with its text: a table of its own)
		m, ok := isMap(value)
		if !ok {
			return nil, no("a table of names, each with its text")
		}
		out := make(map[string]any, len(m))
		for _, k := range sortedKeys(m) {
			if !pairName.MatchString(k) {
				return nil, no(pyRepr(k) + ": a name is letters, digits, _, - and .")
			}
		}
		for _, k := range sortedKeys(m) {
			t, err := text(m[k])
			if err != nil {
				return nil, err
			}
			out[k] = t
		}
		return out, nil
	case "list", "lines":
		if t, ok := value.(string); ok && kind == "list" {
			value = []any{t} // (one item, said alone)
		}
		l, ok := asList(value)
		if !ok || isStr(value) {
			return nil, no("a list")
		}
		out := make([]any, len(l))
		for i, v := range l {
			t, err := text(v)
			if err != nil {
				return nil, err
			}
			out[i] = t
		}
		return out, nil
	}
	if typ, _ := s["type"].(string); kind == "text" && typ == "port" {
		if n, ok := isInt(value); ok {
			if _, err := text(strconv.FormatInt(n, 10)); err != nil {
				return nil, err
			}
			return n, nil
		}
	}
	t, err := text(value)
	if err != nil {
		return nil, err
	}
	return t, nil
}

// ---------------------------------------------------------------- where values are kept

// Store is where the values of one of the app's files are kept.
type Store interface {
	Read() (map[string]any, error)         // the values as they're kept
	Write(parts []string, value any) error // value kept at parts (nil takes it away)
	Name() string                          // how the bubble's "Saved as" names it
}

// MemoryStore is values kept in a map (a draft, a test).
type MemoryStore struct {
	Data map[string]any
	name string
}

// NewMemoryStore is values kept in data (a new map where it's nil), named name.
func NewMemoryStore(data map[string]any, name string) *MemoryStore {
	if data == nil {
		data = map[string]any{}
	}
	if name == "" {
		name = "memory"
	}
	return &MemoryStore{data, name}
}

func (m *MemoryStore) Read() (map[string]any, error)         { return m.Data, nil }
func (m *MemoryStore) Write(parts []string, value any) error { Put(m.Data, parts, value); return nil }
func (m *MemoryStore) Name() string                          { return m.name }

// JSONStore is values kept in a JSON file (made when the first is saved).
type JSONStore struct {
	Path string
	name string
}

// NewJSONStore is values kept in the JSON file at path, named name (its file's name where none is given).
func NewJSONStore(path, name string) *JSONStore {
	if name == "" {
		name = filepath.Base(path)
	}
	return &JSONStore{path, name}
}

func (j *JSONStore) Name() string { return j.name }

// Read is the file's values (an empty map where there's no file yet, or it isn't a table).
func (j *JSONStore) Read() (map[string]any, error) {
	data, err := os.ReadFile(j.Path)
	if errors.Is(err, os.ErrNotExist) {
		return map[string]any{}, nil
	}
	if err != nil {
		return nil, err
	}
	var got any
	dec := json.NewDecoder(bytes.NewReader(data))
	dec.UseNumber()
	if err := dec.Decode(&got); err != nil {
		return nil, fmt.Errorf("%s: %w", j.Path, err)
	}
	m, ok := numbers(got).(map[string]any)
	if !ok {
		return map[string]any{}, nil
	}
	return m, nil
}

// numbers is a JSON value with its numbers as TOML's are: int64 where whole, else float64.
func numbers(v any) any {
	switch x := v.(type) {
	case json.Number:
		if n, err := x.Int64(); err == nil {
			return n
		}
		f, _ := x.Float64()
		return f
	case map[string]any:
		for k, w := range x {
			x[k] = numbers(w)
		}
	case []any:
		for i, w := range x {
			x[i] = numbers(w)
		}
	}
	return v
}

// Write keeps value at parts (nil takes it away), the file written whole, in one step.
func (j *JSONStore) Write(parts []string, value any) error {
	data, err := j.Read()
	if err != nil {
		return err
	}
	Put(data, parts, value)
	var buf bytes.Buffer
	enc := json.NewEncoder(&buf)
	enc.SetEscapeHTML(false)
	enc.SetIndent("", " ")
	if err := enc.Encode(data); err != nil {
		return err
	}
	tmp := j.Path + ".new"
	if err := os.WriteFile(tmp, buf.Bytes(), 0o644); err != nil {
		return err
	}
	return os.Rename(tmp, j.Path)
}

// TomlStore is values read from a TOML file. Written by Writer where the app gives one (its own way of writing its
// file, comments kept); with none it's read-only, and a save says so.
type TomlStore struct {
	Path   string
	Writer func(parts []string, value any) error
	name   string
}

// NewTomlStore is values read from the TOML file at path, named name, written by writer where the app gives one.
func NewTomlStore(path, name string, writer func(parts []string, value any) error) *TomlStore {
	if name == "" {
		name = filepath.Base(path)
	}
	return &TomlStore{path, writer, name}
}

func (t *TomlStore) Name() string { return t.name }

// Read is the file's values (an empty map where there's no file yet).
func (t *TomlStore) Read() (map[string]any, error) {
	data, err := os.ReadFile(t.Path)
	if errors.Is(err, os.ErrNotExist) {
		return map[string]any{}, nil
	}
	if err != nil {
		return nil, err
	}
	got := map[string]any{}
	if err := toml.Unmarshal(data, &got); err != nil {
		return nil, fmt.Errorf("%s: %w", t.Path, err)
	}
	return got, nil
}

// Write writes value at parts with the app's writer (an Error where it gave none: read-only).
func (t *TomlStore) Write(parts []string, value any) error {
	if t.Writer == nil {
		return fail("%s is only read here: the app gives no way to write it", t.name)
	}
	return t.Writer(parts, value)
}

// Put puts value at parts in data; nil takes it away, and each table left empty by that with it (but a list's item,
// whose place says which member it is: it's left, empty). A part "#n" is the nth item of a list.
func Put(data map[string]any, parts []string, value any) {
	putIn(data, parts, value)
}

// place of a list's item in a path ("#1": its first), false where the part isn't one.
func itemAt(p string) (int, bool) {
	if !strings.HasPrefix(p, At) {
		return 0, false
	}
	n, err := strconv.Atoi(p[len(At):])
	return n, err == nil && n > 0
}

func putIn(node any, parts []string, value any) any {
	p, rest := parts[0], parts[1:]
	if n, ok := itemAt(p); ok {
		l, isList := asList(node)
		if !isList || n > len(l) {
			if value == nil {
				return node
			}
			for len(l) < n {
				l = append(l, map[string]any{})
			}
		}
		if len(rest) == 0 {
			l[n-1] = value
			return l
		}
		if l[n-1] = putIn(l[n-1], rest, value); l[n-1] == nil {
			l[n-1] = map[string]any{}
		}
		return l
	}
	m, ok := isMap(node)
	if !ok {
		if value == nil {
			return node
		}
		m = map[string]any{}
	}
	if len(rest) == 0 {
		if value == nil {
			delete(m, p)
		} else {
			m[p] = value
		}
		return m
	}
	under, there := m[p]
	if _, isList := asList(under); !isList {
		if _, isTable := isMap(under); !isTable && value == nil {
			return m
		}
	}
	child := putIn(under, rest, value)
	if left, isTable := isMap(child); (isTable && len(left) == 0) || child == nil {
		if there {
			delete(m, p)
		}
		return m
	}
	m[p] = child
	return m
}

func atPath(data any, parts []string) any {
	for _, p := range parts {
		if n, ok := itemAt(p); ok {
			l, isList := asList(data)
			if !isList || n > len(l) {
				return nil
			}
			data = l[n-1]
			continue
		}
		m, ok := isMap(data)
		if !ok {
			return nil
		}
		if data, ok = m[p]; !ok {
			return nil
		}
	}
	return data
}

// ---------------------------------------------------------------- the settings, loaded

// AnswerFunc answers what the app works out, for a member (nil: none): a default's value (nil: not known yet), a
// choice's choices [[value, label]…], a set's members ([{"id", "name"}…], or their ids), or whether a feature is there.
type AnswerFunc func(member []string) any

// Default is a setting's default as it stands.
type Default struct {
	Value any    `json:"value"` // nil: none, or not known yet
	Says  string `json:"says"`  // how it's worked out, in words ("" for a fixed one)
	How   string `json:"how"`   // fixed, worked_out, required, shared (a set's value for every member) or cascade
}

// Member is one member of a set.
type Member struct {
	ID   string `json:"id"`
	Name string `json:"name"`
}

// Catalog is a project's settings as its file says them, and the answer for each by its id.
type Catalog struct {
	Where    string
	App      string
	Groups   map[string]map[string]any
	Sets     map[string]map[string]any
	Settings map[string]map[string]any
	Added    map[string]bool // the ids added as data: no code names them, and that's not an orphan
	order    Order
	stores   map[string]Store
	answers  map[string]AnswerFunc
}

// New is the catalog of a settings file read as data (from where), held to the format first. Its groups, sets and
// settings are in the order ord says (those it doesn't: sorted).
func New(data map[string]any, where string, ord Order) (*Catalog, error) {
	if bad := problems(data, false, ord); len(bad) > 0 {
		return nil, notInFormat(where+" isn't in the settings format:", bad)
	}
	c := &Catalog{Where: where, Added: map[string]bool{}, order: Order{}, stores: map[string]Store{}, answers: map[string]AnswerFunc{}}
	f, _ := isMap(data["format"])
	c.App, _ = f["app"].(string)
	c.take(data, ord)
	return c, nil
}

func notInFormat(head string, bad []Problem) error {
	lines := []string{head}
	for _, p := range bad {
		lines = append(lines, "  "+p.Line)
	}
	return &Error{strings.Join(lines, "\n")}
}

func (c *Catalog) take(data map[string]any, ord Order) {
	tables := map[string]map[string]map[string]any{}
	for _, k := range []string{"groups", "sets", "settings"} {
		src, _ := isMap(data[k])
		out := map[string]map[string]any{}
		for id, v := range src {
			m, _ := isMap(v)
			cp := make(map[string]any, len(m))
			for kk, vv := range m {
				cp[kk] = vv
			}
			out[id] = cp
		}
		tables[k] = out
		c.order[k] = ordered(out, ord[k])
	}
	c.Groups, c.Sets, c.Settings = tables["groups"], tables["sets"], tables["settings"]
	for _, s := range c.Settings {
		if under, ok := s["under"].(string); ok {
			if _, there := s["group"]; !there {
				s["group"] = orEmpty(c.Settings[under], "group")
			}
		}
	}
}

// IDs is the settings' ids, in the file's order (those added after them).
func (c *Catalog) IDs() []string { return append([]string(nil), c.order["settings"]...) }

// GroupIDs is the groups' ids, in the file's order.
func (c *Catalog) GroupIDs() []string { return append([]string(nil), c.order["groups"]...) }

// Store says where the values of the file settings call file are kept.
func (c *Catalog) Store(file string, store Store) Store {
	c.stores[file] = store
	return store
}

// Answer gives what the app works out by that name (a default_from, a choices_from, a members_from, a feature).
func (c *Catalog) Answer(name string, fn AnswerFunc) { c.answers[name] = fn }

// Add adds settings that are themselves data to the file's, held to the format with them.
func (c *Catalog) Add(settings, groups, sets map[string]any) error {
	merged := func(have map[string]map[string]any, more map[string]any) map[string]any {
		out := map[string]any{}
		for k, v := range have {
			out[k] = v
		}
		for k, v := range more {
			out[k] = v
		}
		return out
	}
	data := map[string]any{"format": map[string]any{"version": int64(Format), "app": c.App},
		"groups": merged(c.Groups, groups), "sets": merged(c.Sets, sets), "settings": merged(c.Settings, settings)}
	ord := Order{}
	for k, more := range map[string]map[string]any{"groups": groups, "sets": sets, "settings": settings} {
		ord[k] = append(append([]string(nil), c.order[k]...), sortedKeys(more)...)
	}
	if bad := problems(data, false, ord); len(bad) > 0 {
		return notInFormat("these settings aren't in the settings format:", bad)
	}
	c.take(data, ord)
	for id := range settings {
		c.Added[id] = true
	}
	return nil
}

// SettingOf is the setting's definition (an Error where there's none by that id).
func (c *Catalog) SettingOf(id string) (map[string]any, error) {
	s, ok := c.Settings[id]
	if !ok {
		return nil, fail("no setting %s in %s", pyRepr(id), c.Where)
	}
	return s, nil
}

// SubsOf is its sub-settings' ids, in the file's order.
func (c *Catalog) SubsOf(id string) []string {
	var out []string
	for _, k := range c.order["settings"] {
		if under, _ := c.Settings[k]["under"].(string); under == id {
			out = append(out, k)
		}
	}
	return out
}

func (c *Catalog) members(s map[string]any, member []string) ([]string, error) {
	es := each(s)
	allShared := len(es) > 0
	for _, e := range es {
		if c.Sets[e]["shared"] != true {
			allShared = false
		}
	}
	if len(member) != len(es) && !(len(member) == 0 && allShared) {
		sets := strings.Join(es, " and ")
		if sets == "" {
			sets = "nothing"
		}
		got := "no member"
		if len(member) > 0 {
			got = pyStr(strs(member))
		}
		return nil, fail("%s: it's one for each of %s: asked for with %s", pyStr(s["label"]), sets, got)
	}
	if len(member) == 0 {
		out := make([]string, len(es))
		for i := range out {
			out[i] = Shared
		}
		return out, nil
	}
	return member, nil
}

// WhereSaved is the file (by the name settings call it) and the path in it the setting's value is at.
func (c *Catalog) WhereSaved(id string, member ...string) (string, []string, error) {
	s, err := c.SettingOf(id)
	if err != nil {
		return "", nil, err
	}
	got, err := c.members(s, member)
	if err != nil {
		return "", nil, err
	}
	file, parts := c.place(s, got)
	return file, parts, nil
}

// anyMember is a member that stands for any one: what Unknown looks for in a member's place.
const anyMember = "\x00"

func split(key string) []string {
	var out []string
	for _, p := range strings.Split(key, ".") {
		if p != "" {
			out = append(out, p)
		}
	}
	return out
}

// place is the file and path of a setting's value for those members (one a set: "*" the value every member shares,
// kept where the set's shared_saved says, else as a member named "*" beside the others). The setting's own file where
// it says one, else its set's.
func (c *Catalog) place(s map[string]any, got []string) (string, []string) {
	saved, _ := isMap(s["saved"])
	file, _ := saved["file"].(string)
	var parts []string
	for i, e := range each(s) {
		set := c.Sets[e]
		m := got[i]
		ss, sharedSaved := isMap(set["shared_saved"])
		if m == Shared && sharedSaved {
			if file == "" {
				file, _ = ss["file"].(string)
			}
			key, _ := ss["key"].(string)
			parts = append(parts, split(key)...)
		} else {
			own, _ := isMap(set["saved"])
			key, _ := own["key"].(string)
			if set["list"] == true && m != anyMember && !strings.HasPrefix(m, "<") {
				m = At + m // (a list's item, by its place)
			}
			parts = append(append(parts, split(key)...), m)
		}
		if file == "" {
			own, _ := isMap(set["saved"])
			file, _ = own["file"].(string)
		}
	}
	key, _ := saved["key"].(string)
	return file, append(parts, strings.Split(key, ".")...)
}

func (c *Catalog) store(file string) (Store, error) {
	st, ok := c.stores[file]
	if !ok {
		return nil, fail("the app hasn't said where the values of %s are kept (cat.Store)", pyRepr(file))
	}
	return st, nil
}

// SavedValue is what's saved for it (nil: nothing).
func (c *Catalog) SavedValue(id string, member ...string) (any, error) {
	file, parts, err := c.WhereSaved(id, member...)
	if err != nil {
		return nil, err
	}
	st, err := c.store(file)
	if err != nil {
		return nil, err
	}
	data, err := st.Read()
	if err != nil {
		return nil, err
	}
	return atPath(data, parts), nil
}

func (c *Catalog) ask(name string, member []string, what string) (any, error) {
	fn, ok := c.answers[name]
	if !ok {
		return nil, fail("the app doesn't answer %s (%s: cat.Answer)", pyRepr(name), what)
	}
	return fn(member), nil
}

// DefaultOf is its default as it stands.
func (c *Catalog) DefaultOf(id string, member ...string) (Default, error) {
	s, err := c.SettingOf(id)
	if err != nil {
		return Default{}, err
	}
	got, err := c.members(s, member)
	if err != nil {
		return Default{}, err
	}
	es := each(s)
	if len(got) > 0 {
		someone, allShared := false, true
		for _, m := range got {
			if m != Shared {
				someone = true
			}
		}
		for _, e := range es {
			if c.Sets[e]["shared"] != true {
				allShared = false
			}
		}
		if someone && allShared {
			every := make([]string, len(got))
			for i := range every {
				every[i] = Shared
			}
			shared, err := c.SavedValue(id, every...)
			if err != nil {
				return Default{}, err
			}
			if shared != nil {
				return Default{shared, fmt.Sprintf("every %s's", pyStr(c.Sets[es[len(es)-1]]["member"])), "shared"}, nil
			}
		}
	}
	before, name, err := c.before(s, got)
	if err != nil {
		return Default{}, err
	}
	if before != nil { // (a set that cascades: what the member before it comes to, its own or what that one takes in turn)
		v, err := c.ValueOf(id, before...)
		if err != nil {
			return Default{}, err
		}
		return Default{v, name + "'s", "cascade"}, nil
	}
	if d, ok := s["default"]; ok {
		return Default{d, "", "fixed"}, nil
	}
	if from, ok := s["default_from"].(string); ok {
		v, err := c.ask(from, member, id+"'s default_from")
		if err != nil {
			return Default{}, err
		}
		return Default{v, pyStr(s["default_says"]), "worked_out"}, nil
	}
	return Default{nil, "", "required"}, nil
}

// before is, in a set that cascades (its members in the order the app answers them, each taking the value of the one
// before it unless it has its own): the members a setting's value is taken from, and that member's name — nil for the
// first member, and for a setting in no such set. The innermost set that cascades is the one followed.
func (c *Catalog) before(s map[string]any, got []string) ([]string, string, error) {
	es := each(s)
	for i := len(es) - 1; i >= 0; i-- {
		if c.Sets[es[i]]["cascade"] != true || i >= len(got) {
			continue
		}
		var inside []string
		if i > 0 {
			inside = []string{got[i-1]}
		}
		listed, err := c.MembersOf(es[i], inside...)
		if err != nil {
			return nil, "", err
		}
		at := 0
		for j, m := range listed {
			if m.ID == got[i] {
				at = j
				break
			}
		}
		if at > 0 {
			out := append(append(append([]string(nil), got[:i]...), listed[at-1].ID), got[i+1:]...)
			return out, listed[at-1].Name, nil
		}
	}
	return nil, "", nil
}

// ValueOf is the value that holds: what's saved, else its default as it stands.
func (c *Catalog) ValueOf(id string, member ...string) (any, error) {
	v, err := c.SavedValue(id, member...)
	if err != nil || v != nil {
		return v, err
	}
	d, err := c.DefaultOf(id, member...)
	return d.Value, err
}

// ChoicesOf is a choice's [[value, label]…] (the app's answer for a choices_from); nil for a setting that isn't one.
func (c *Catalog) ChoicesOf(id string, member ...string) ([]any, error) {
	s, err := c.SettingOf(id)
	if err != nil {
		return nil, err
	}
	if l, ok := asList(s["choices"]); ok {
		return l, nil
	}
	from, ok := s["choices_from"].(string)
	if !ok {
		return nil, nil
	}
	got, err := c.ask(from, member, id+"'s choices_from")
	if err != nil {
		return nil, err
	}
	l, _ := asList(got)
	out := make([]any, len(l))
	for i, x := range l {
		pair, _ := asList(x)
		out[i] = pair
	}
	return out, nil
}

// Held is value as the setting keeps it (an Error where it can't take it).
func (c *Catalog) Held(id string, value any, member ...string) (any, error) {
	s, err := c.SettingOf(id)
	if err != nil {
		return nil, err
	}
	var values []any
	check := false
	if s["kind"] == "choice" {
		choices, err := c.ChoicesOf(id, member...)
		if err != nil {
			return nil, err
		}
		if choices != nil {
			check = true
			for _, ch := range choices {
				if pair, _ := asList(ch); len(pair) > 0 {
					values = append(values, pair[0])
				}
			}
		}
	}
	return held(id, s, value, values, check)
}

func empty(v any) bool {
	if v == nil {
		return true
	}
	if s, ok := v.(string); ok {
		return s == ""
	}
	if l, ok := asList(v); ok {
		return len(l) == 0
	}
	return false
}

// SaveValue saves the value, held to the setting first; nil (or "" or an empty list) takes it away: back to its
// default.
func (c *Catalog) SaveValue(id string, value any, member ...string) error {
	file, parts, err := c.WhereSaved(id, member...)
	if err != nil {
		return err
	}
	if empty(value) {
		value = nil
	} else {
		if value, err = c.Held(id, value, member...); err != nil {
			return err
		}
		took, err := c.DefaultOf(id, member...)
		if err != nil {
			return err
		}
		if took.How == "cascade" && same(took.Value, value) {
			value = nil // (the same as it would take from the member before it: nothing of its own to keep)
		}
	}
	st, err := c.store(file)
	if err != nil {
		return err
	}
	return st.Write(parts, value)
}

// MembersOf is the set's members as the app answers them now (a set inside another: of member).
func (c *Catalog) MembersOf(set string, member ...string) ([]Member, error) {
	g, ok := c.Sets[set]
	if !ok {
		return nil, fail("no set %s in %s", pyRepr(set), c.Where)
	}
	from, _ := g["members_from"].(string)
	got, err := c.ask(from, member, "the "+set+" set's members_from")
	if err != nil {
		return nil, err
	}
	l, _ := asList(got)
	out := make([]Member, 0, len(l))
	for _, m := range l {
		switch x := m.(type) {
		case Member:
			if x.Name == "" {
				x.Name = x.ID
			}
			out = append(out, x)
		case map[string]any:
			name := x["name"]
			if !truthy(name) {
				name = x["id"]
			}
			out = append(out, Member{pyStr(x["id"]), pyStr(name)})
		case map[string]string:
			name := x["name"]
			if name == "" {
				name = x["id"]
			}
			out = append(out, Member{x["id"], name})
		default:
			out = append(out, Member{pyStr(m), pyStr(m)})
		}
	}
	return out, nil
}

// Shown says whether it's on the page at all (its shown_when; a sub-setting's parent's).
func (c *Catalog) Shown(id string, member ...string) (bool, error) {
	s, err := c.SettingOf(id)
	if err != nil {
		return false, err
	}
	if under, ok := s["under"].(string); ok {
		return c.Shown(under, member...)
	}
	when, ok := isMap(s["shown_when"])
	if !ok || len(when) == 0 {
		return true, nil
	}
	if feature, ok := when["feature"].(string); ok {
		got, err := c.ask(feature, member, id+"'s shown_when")
		return truthy(got), err
	}
	other, _ := when["setting"].(string)
	o, err := c.SettingOf(other)
	if err != nil {
		return false, err
	}
	var m []string
	if len(each(o)) > 0 {
		m = member
	}
	v, err := c.ValueOf(other, m...)
	return same(v, when["is"]), err
}

// SavedSays is how the bubble names where it's saved: {"key": "servers.<server>.url", "file": the store's name}
// (shared: the value every member of its set shares).
func (c *Catalog) SavedSays(id string, shared bool) (map[string]string, error) {
	s, err := c.SettingOf(id)
	if err != nil {
		return nil, err
	}
	var got []string
	for _, e := range each(s) {
		if shared && c.Sets[e]["shared"] == true {
			got = append(got, Shared)
		} else {
			got = append(got, "<"+pyStr(c.Sets[e]["member"])+">")
		}
	}
	file, parts := c.place(s, got)
	name := file
	if st, ok := c.stores[file]; ok && st.Name() != "" {
		name = st.Name()
	}
	return map[string]string{"key": strings.Join(parts, "."), "file": name}, nil
}

// State is one setting as it stands, for the drawer: its value (a secret's never: only whether one is set), its
// default, its choices where the app answers them, and whether it's shown.
func (c *Catalog) State(id string, member ...string) (map[string]any, error) {
	s, err := c.SettingOf(id)
	if err != nil {
		return nil, err
	}
	v, err := c.SavedValue(id, member...)
	if err != nil {
		return nil, err
	}
	d, err := c.DefaultOf(id, member...)
	if err != nil {
		return nil, err
	}
	shown, err := c.Shown(id, member...)
	if err != nil {
		return nil, err
	}
	out := map[string]any{"default": d, "shown": shown}
	if s["kind"] == "secret" {
		out["set"] = v != nil
	} else {
		out["value"] = v
	}
	if _, ok := s["choices_from"]; ok {
		if out["choices"], err = c.ChoicesOf(id, member...); err != nil {
			return nil, err
		}
	}
	return out, nil
}

// Ordered is a JSON object whose keys are written in the order given (as Python's dicts are).
type Ordered struct {
	Keys   []string
	Values map[string]any
}

func (o *Ordered) set(k string, v any) {
	if _, ok := o.Values[k]; !ok {
		o.Keys = append(o.Keys, k)
	}
	o.Values[k] = v
}

func newOrdered() *Ordered { return &Ordered{Values: map[string]any{}} }

// MarshalJSON writes the object, its keys in order.
func (o *Ordered) MarshalJSON() ([]byte, error) {
	var buf bytes.Buffer
	buf.WriteByte('{')
	for i, k := range o.Keys {
		if i > 0 {
			buf.WriteByte(',')
		}
		key, _ := json.Marshal(k)
		val, err := json.Marshal(o.Values[k])
		if err != nil {
			return nil, err
		}
		buf.Write(key)
		buf.WriteByte(':')
		buf.Write(val)
	}
	buf.WriteByte('}')
	return buf.Bytes(), nil
}

// Page is everything the drawer needs, as JSON: the groups and sets, each setting's definition (its sub-settings and
// how its saved place is said with it), and each setting's state — one for a setting in no set, and for one in a set
// one for each member the app answers (members: {set: [ids]} to say them here instead; a shared set's "*" with them).
// A state's key is the setting's id, then each member's after a "|".
func (c *Catalog) Page(members map[string][]string) (*Ordered, error) {
	defs := newOrdered()
	for _, id := range c.order["settings"] {
		s := c.Settings[id]
		def := map[string]any{}
		for k, v := range s {
			if k != "each" {
				def[k] = v
			}
		}
		says, err := c.SavedSays(id, false)
		if err != nil {
			return nil, err
		}
		subs := c.SubsOf(id)
		if subs == nil {
			subs = []string{}
		}
		es := each(s)
		if es == nil {
			es = []string{}
		}
		def["id"], def["each"], def["subs"], def["saved_says"] = id, es, subs, says
		for _, e := range es {
			if c.Sets[e]["shared"] == true {
				if def["saved_says_shared"], err = c.SavedSays(id, true); err != nil {
					return nil, err
				}
			}
		}
		defs.set(id, def)
	}
	states := newOrdered()
	for _, id := range c.order["settings"] {
		every, err := c.every(each(c.Settings[id]), members, nil)
		if err != nil {
			return nil, err
		}
		for _, m := range every {
			st, err := c.State(id, m...)
			if err != nil {
				return nil, err
			}
			states.set(strings.Join(append([]string{id}, m...), "|"), st)
		}
	}
	groups, sets := newOrdered(), newOrdered()
	for _, id := range c.order["groups"] {
		groups.set(id, c.Groups[id])
	}
	for _, id := range c.order["sets"] {
		v := c.Sets[id]
		sets.set(id, map[string]any{"title": v["title"], "member": v["member"], "shared": v["shared"] == true, "cascade": v["cascade"] == true, "list": v["list"] == true})
	}
	out := newOrdered()
	out.set("format", Format)
	out.set("groups", groups)
	out.set("sets", sets)
	out.set("settings", defs)
	out.set("states", states)
	return out, nil
}

func (c *Catalog) every(es []string, members map[string][]string, inside []string) ([][]string, error) {
	if len(es) == 0 {
		return [][]string{{}}, nil
	}
	set := es[0]
	ids, given := members[set]
	if !given {
		var in []string
		if len(inside) > 0 {
			in = inside[len(inside)-1:]
		}
		listed, err := c.MembersOf(set, in...)
		if err != nil {
			return nil, err
		}
		for _, m := range listed {
			ids = append(ids, m.ID)
		}
	}
	if c.Sets[set]["shared"] == true {
		ids = append([]string{Shared}, ids...)
	}
	var out [][]string
	for _, m := range ids {
		rest, err := c.every(es[1:], members, append(append([]string(nil), inside...), m))
		if err != nil {
			return nil, err
		}
		for _, r := range rest {
			out = append(out, append([]string{m}, r...))
		}
	}
	return out, nil
}

// Unknown is the values saved in file at a key no setting has, as dotted paths (tables: only under those top keys —
// the part of a file that's been given over to the settings; nil: all of it).
func (c *Catalog) Unknown(file string, tables []string) ([]string, error) {
	var patterns [][]string
	for _, id := range c.order["settings"] {
		s := c.Settings[id]
		combos := [][]string{{}}
		for _, e := range each(s) {
			choices := []string{anyMember}
			if _, ok := c.Sets[e]["shared_saved"]; ok {
				choices = append(choices, Shared)
			}
			var next [][]string
			for _, combo := range combos {
				for _, ch := range choices {
					next = append(next, append(append([]string(nil), combo...), ch))
				}
			}
			combos = next
		}
		for _, got := range combos {
			if f, parts := c.place(s, got); f == file {
				patterns = append(patterns, parts)
			}
		}
	}
	var out []string
	var walk func(node any, path []string)
	walk = func(node any, path []string) {
		var fits [][]string
		for _, p := range patterns {
			if len(p) < len(path) {
				continue
			}
			ok := true
			for i, a := range path {
				if p[i] != anyMember && p[i] != a {
					ok = false
					break
				}
			}
			if ok {
				fits = append(fits, p)
			}
		}
		for _, p := range fits {
			if len(p) == len(path) {
				return
			}
		}
		if m, ok := isMap(node); ok && len(fits) > 0 {
			for _, k := range sortedKeys(m) {
				walk(m[k], append(append([]string(nil), path...), k))
			}
			return
		}
		if l, ok := asList(node); ok && !isStr(node) && len(fits) > 0 { // (a list set's items: each in a member's place)
			for i, v := range l {
				walk(v, append(append([]string(nil), path...), At+strconv.Itoa(i+1)))
			}
			return
		}
		out = append(out, strings.Join(path, "."))
	}
	st, err := c.store(file)
	if err != nil {
		return nil, err
	}
	data, err := st.Read()
	if err != nil {
		return nil, err
	}
	for _, k := range sortedKeys(data) {
		if tables == nil || has(tables, k) {
			walk(data[k], []string{k})
		}
	}
	return out, nil
}

// Parse is the settings file src (named where), held to the format, its groups, sets and settings in its own order.
func Parse(src []byte, where string) (*Catalog, error) {
	data := map[string]any{}
	if err := toml.Unmarshal(src, &data); err != nil {
		return nil, fail("%s: %v", where, err)
	}
	return New(data, where, orderOf(src))
}

// orderOf is the order a TOML file first says each [groups.x], [sets.x] and [settings.x] in.
func orderOf(src []byte) Order {
	ord, seen := Order{}, map[string]bool{}
	var p unstable.Parser
	p.Reset(src)
	var table []string
	note := func(path []string) {
		if len(path) >= 2 && !seen[path[0]+"."+path[1]] {
			seen[path[0]+"."+path[1]] = true
			ord[path[0]] = append(ord[path[0]], path[1])
		}
	}
	keyOf := func(n *unstable.Node) []string {
		var out []string
		it := n.Key()
		for it.Next() {
			out = append(out, string(it.Node().Data))
		}
		return out
	}
	for p.NextExpression() {
		e := p.Expression()
		switch e.Kind {
		case unstable.Table, unstable.ArrayTable:
			table = keyOf(e)
			note(table)
		case unstable.KeyValue:
			note(append(append([]string(nil), table...), keyOf(e)...))
		}
	}
	return ord
}

// Load is the settings file at path, held to the format (an Error: it can't be read, or everything wrong with it).
func Load(path string) (*Catalog, error) {
	src, err := os.ReadFile(path)
	if err != nil {
		return nil, fail("%s: %v", path, err)
	}
	return Parse(src, filepath.Base(path))
}
