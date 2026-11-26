package settingslib

// The Go reader's own tests: the same settings file as the Python reader's tests (sample.toml, beside this), read the
// same way, the same answers. A copy of the template, kept in step with it: never edit it here.

import (
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"reflect"
	"regexp"
	"strings"
	"testing"

	toml "github.com/pelletier/go-toml/v2"
)

func sampleData(t *testing.T) map[string]any {
	t.Helper()
	src, err := os.ReadFile("sample.toml")
	if err != nil {
		t.Fatal(err)
	}
	data := map[string]any{}
	if err := toml.Unmarshal(src, &data); err != nil {
		t.Fatal(err)
	}
	return data
}

func sampleSrc(t *testing.T) []byte {
	t.Helper()
	src, err := os.ReadFile("sample.toml")
	if err != nil {
		t.Fatal(err)
	}
	return src
}

type stores struct{ project, computer *MemoryStore }

func sample(t *testing.T, project, computer map[string]any) (*Catalog, stores) {
	t.Helper()
	cat, err := Parse(sampleSrc(t), "sample.toml")
	if err != nil {
		t.Fatal(err)
	}
	st := stores{NewMemoryStore(project, "app.toml"), NewMemoryStore(computer, "app.local.toml")}
	cat.Store("project", st.project)
	cat.Store("computer", st.computer)
	cat.Answer("manifest_domain", func([]string) any { return "my_thing" })
	cat.Answer("formulas", func([]string) any { return [][]string{{"nas", "TrueNAS app"}, {"ha", "Home Assistant"}} })
	cat.Answer("servers", func([]string) any { return []any{map[string]any{"id": "nas1", "name": "The NAS"}, "nas2"} })
	cat.Answer("gates", func([]string) any { return []string{"push", "merge"} })
	return cat, st
}

func settingsOf(data map[string]any) map[string]any { return data["settings"].(map[string]any) }

func setting(data map[string]any, id string) map[string]any {
	return settingsOf(data)[id].(map[string]any)
}

func infoOf(data map[string]any, id string) map[string]any {
	return setting(data, id)["info"].(map[string]any)
}

// broken is what's wrong with the sample once change has changed it.
func broken(t *testing.T, change func(d map[string]any)) []Problem {
	t.Helper()
	d := sampleData(t)
	change(d)
	return Problems(d)
}

func lines(got []Problem, check string) string {
	var out []string
	for _, p := range got {
		if check == "" || p.Check == check {
			out = append(out, p.Line)
		}
	}
	return strings.Join(out, "\n")
}

func must(t *testing.T, err error) {
	t.Helper()
	if err != nil {
		t.Fatal(err)
	}
}

func eq(t *testing.T, got, want any) {
	t.Helper()
	if !same(got, want) && !reflect.DeepEqual(got, want) {
		t.Fatalf("got %#v, want %#v", got, want)
	}
}

func matches(t *testing.T, got, pattern string) {
	t.Helper()
	if !regexp.MustCompile(pattern).MatchString(got) {
		t.Fatalf("%q doesn't say %q", got, pattern)
	}
}

func failsWith(t *testing.T, err error, pattern string) {
	t.Helper()
	if err == nil {
		t.Fatalf("no error, and one saying %q was wanted", pattern)
	}
	matches(t, err.Error(), pattern)
}

func TestAFileInTheFormatHasNothingWrong(t *testing.T) {
	if got := Problems(sampleData(t)); len(got) != 0 {
		t.Fatal(got)
	}
	cat, _ := sample(t, nil, nil)
	eq(t, cat.SubsOf("host"), []string{"port"})
	eq(t, cat.IDs(), []string{"host", "port", "domain", "mode", "server_url", "server_limit", "token"})
	eq(t, cat.GroupIDs(), []string{"net", "server"})
}

func TestASettingWithNoInfoCantBeLoaded(t *testing.T) {
	long := "Tells a push which machine on your network to copy the project to and then restart it on, over one connection."
	for _, id := range []string{"host", "port", "server_limit"} {
		for _, c := range []struct {
			change func(d map[string]any)
			why    string
		}{
			{func(d map[string]any) { delete(setting(d, id), "info") }, "info: missing"},
			{func(d map[string]any) { setting(d, id)["info"] = "The machine's address. Its Network page shows it." }, "info: missing"},
			{func(d map[string]any) { delete(infoOf(d, id), "does") }, `info.does: missing \(what it does\)`},
			{func(d map[string]any) { delete(infoOf(d, id), "config") }, `info.config: missing \(its configuration: what to give it\)`},
			{func(d map[string]any) { infoOf(d, id)["does"] = "  " }, "info.does: what it does, in words"},
			{func(d map[string]any) { infoOf(d, id)["does"] = "An open finding." }, "info.does: 16 letters, and what it does takes 80 at least"},
			{func(d map[string]any) { infoOf(d, id)["does"] = strings.TrimRight(long, ".") }, "info.does: one or more sentences"},
			{func(d map[string]any) { infoOf(d, id)["why"] = "Because." }, "info.why: not a part of it"},
			{func(d map[string]any) {
				label := setting(d, id)["label"].(string)
				said := strings.TrimSpace(strings.Repeat(label+" ", 30))
				infoOf(d, id)["does"] = strings.ToUpper(said[:1]) + said[1:] + "."
			}, `info.does: says its label and summary again \(0 words of its own, and it takes 6\)`},
			{func(d map[string]any) { infoOf(d, id)["config"] = infoOf(d, id)["does"] }, `info.config: says its label, its summary and the parts before it again \(0 words`},
		} {
			got := broken(t, c.change)
			for _, p := range got {
				if p.Check != "info" && p.Check != "format" {
					t.Fatalf("%s %s: %v", id, c.why, got)
				}
			}
			matches(t, lines(got, "info"), `\[settings.`+id+`\] `+c.why)
		}
	}
	matches(t, lines(broken(t, func(d map[string]any) { delete(infoOf(d, "host"), "when") }), ""), `\[settings.host\] info.when: missing \(when to use it\)`)
	matches(t, lines(broken(t, func(d map[string]any) { infoOf(d, "host")["when"] = infoOf(d, "host")["does"] }), ""), `info.when: says its label, its summary and the parts before it again`)
	eq(t, len(broken(t, func(d map[string]any) { delete(infoOf(d, "port"), "when") })), 0) // (a sub-setting: its parent says when)
	sw := func() map[string]any {
		return map[string]any{"label": "Hold syncs", "info": map[string]any{"does": long, "when": "Turn it on while people are watching in the evening, and off again once the house has gone to bed for the night."},
			"kind": "switch", "group": "net", "saved": map[string]any{"file": "project", "key": "hold"}, "default": false}
	}
	eq(t, len(broken(t, func(d map[string]any) { settingsOf(d)["hold"] = sw() })), 0) // (a switch is on or off: no configuration to say)
	matches(t, lines(broken(t, func(d map[string]any) { s := sw(); s["kind"] = "text"; settingsOf(d)["hold"] = s }), ""), `\[settings.hold\] info.config: missing`)
	d := sampleData(t)
	delete(setting(d, "port"), "info")
	_, err := New(d, "sample.toml", nil)
	failsWith(t, err, `\[settings.port\] info: missing`)
	got := broken(t, func(d map[string]any) { setting(d, "host")["summary"] = "the address (default: nas)" })
	eq(t, len(got), 1)
	eq(t, got[0].Check, "info")
	eq(t, InfoProblems(map[string]any{"label": "x", "info": map[string]any{"does": 7}}, false)[0], "info.does: what it does, in words")
}

func TestEachRuleOfTheFormatIsHeld(t *testing.T) {
	type d = map[string]any
	for _, c := range []struct {
		change     func(d)
		check, why string
	}{
		{func(x d) { setting(x, "host")["colour"] = "red" }, "format", "colour: not a field of a setting"},
		{func(x d) { setting(x, "host")["kind"] = "dial" }, "format", "kind = 'dial'"},
		{func(x d) { setting(x, "host")["group"] = "nowhere" }, "format", "no such group"},
		{func(x d) { delete(setting(x, "host"), "saved") }, "format", "saved: missing"},
		{func(x d) { setting(x, "server_url")["each"] = "racks" }, "format", "no such set"},
		{func(x d) { setting(x, "server_url")["saved"] = d{"file": "", "key": "url"} }, "format", "which of the app's files"},
		{func(x d) { x["extras"] = d{} }, "format", `\[extras\]: not a table`},
		{func(x d) { x["format"].(d)["version"] = int64(9) }, "format", "version = 1: missing"},
		{func(x d) { x["format"].(d)["colour"] = "red" }, "format", `\[format\] colour: not a key of it`},
		{func(x d) { setting(x, "domain")["shown_when"] = d{"setting": "nothing", "is": 1} }, "format", "shown_when"},
		{func(x d) { x["groups"].(d)["Bad"] = d{"title": "B"} }, "format", `\[groups.Bad\]: an id is`},
		{func(x d) { x["groups"].(d)["odd"] = "x" }, "format", `\[groups.odd\]: a table`},
		{func(x d) { x["groups"].(d)["net"].(d)["colour"] = 1 }, "format", `\[groups.net\] colour: not a key of it \(title, page, info\)`},
		{func(x d) { delete(x["sets"].(d)["servers"].(d), "member") }, "format", `\[sets.servers\] member: missing`},
		{func(x d) { x["sets"].(d)["servers"].(d)["saved"] = d{"file": "project", "key": "a b", "x": 1} }, "format", `saved.x: not a key of it[^\n]*\n[^\n]*saved.key: the dotted path`},
		{func(x d) { settingsOf(x)["Bad"] = setting(x, "host") }, "format", `\[settings.Bad\]: an id is`},
		{func(x d) { settingsOf(x)["odd"] = 1 }, "format", `\[settings.odd\]: a table`},
		{func(x d) { delete(setting(x, "host"), "group") }, "format", `\[settings.host\] group: missing`},
		{func(x d) { setting(x, "server_limit")["each"] = []any{"servers", "gates", "servers"} }, "format", "two sets at the most"},
		{func(x d) { x["sets"] = "none" }, "format", `\[sets\]: a table of them`},
		{func(x d) { setting(x, "host")["default"] = "nas.local" }, "defaults", "more than one way"},
		{func(x d) { delete(setting(x, "port"), "default") }, "defaults", "no way"},
		{func(x d) { delete(setting(x, "domain"), "default_says") }, "defaults", "go together"},
		{func(x d) { delete(setting(x, "token"), "revert_why") }, "defaults", "says why"},
		{func(x d) { setting(x, "port")["under"] = "nothing" }, "subsettings", "no such setting"},
		{func(x d) { delete(setting(x, "domain"), "shown_when"); setting(x, "domain")["under"] = "port" }, "subsettings", "one level only"},
		{func(x d) { setting(x, "port")["group"] = "server" }, "subsettings", "its parent's"},
		{func(x d) { setting(x, "port")["each"] = "servers" }, "subsettings", `each: its parent's set \(\[\]\)`},
		{func(x d) { setting(x, "port")["shown_when"] = d{"feature": "x"} }, "subsettings", "shown when its parent is"},
		{func(x d) { setting(x, "mode")["choices"] = []any{[]any{"nas", "NAS"}} }, "fields", "choices or choices_from"},
		{func(x d) { delete(setting(x, "mode"), "choices_from"); setting(x, "mode")["choices"] = []any{"nas"} }, "fields", `choices: \[\[value`},
		{func(x d) { delete(setting(x, "server_limit"), "max") }, "fields", "its min and max"},
		{func(x d) { setting(x, "server_limit")["min"] = int64(500) }, "fields", "its min is over its max"},
		{func(x d) { setting(x, "server_limit")["default"] = int64(500) }, "fields", "default: Limit: from 1 to 100"},
		{func(x d) { setting(x, "server_limit")["default"] = "fast" }, "fields", "default: Limit: a number"},
		{func(x d) { setting(x, "host")["pattern"] = "[a-z]+" }, "fields", "pattern_says"},
		{func(x d) { setting(x, "host")["pattern"] = "[a-z" }, "fields", "pattern: error parsing regexp"},
		{func(x d) { setting(x, "host")["type"] = "colour" }, "fields", "type = 'colour': for a text"},
		{func(x d) { setting(x, "port")["default"] = int64(99999) }, "fields", "a port"},
		{func(x d) { setting(x, "token")["default"] = "x" }, "fields", "a secret has no default"},
		{func(x d) { setting(x, "domain")["saved"] = d{"file": "computer", "key": "tour.host"} }, "unique", "(host|domain) is saved there too"}, // (Problems on a map: the settings in sorted order, so either is the second)
		{func(x d) {
			s := d{}
			for k, v := range setting(x, "domain") {
				s[k] = v
			}
			s["shown_when"] = d{"setting": "mode", "is": "ha"}
			settingsOf(x)["domain2"] = s
		}, "unique", "domain is saved there too"},
	} {
		got := broken(t, c.change)
		found := false
		for _, p := range got {
			found = found || p.Check == c.check
		}
		if !found {
			t.Fatalf("%s: %v", c.why, got)
		}
		matches(t, lines(got, c.check), c.why)
	}
	eq(t, len(broken(t, func(x d) { setting(x, "host")["demo_colour"] = "red" })), 0) // (a field of the app's own)
	// two settings one value picks between (never shown together) may be kept in one place
	eq(t, len(broken(t, func(x d) {
		s := d{}
		for k, v := range setting(x, "domain") {
			s[k] = v
		}
		s["shown_when"] = d{"setting": "mode", "is": "nas"}
		settingsOf(x)["domain2"] = s
	})), 0)
	// settings added as data have no [format] of their own
	eq(t, len(problems(d{"format": d{"app": "demo"}, "settings": d{}}, true, nil)), 0)
}

func TestAValueIsAskedForAndSavedByItsID(t *testing.T) {
	cat, st := sample(t, nil, map[string]any{"tour": map[string]any{"host": "nas.local"}})
	for id, want := range map[string]any{"host": "nas.local", "port": 22, "domain": "my_thing"} {
		v, err := cat.ValueOf(id)
		must(t, err)
		eq(t, v, want)
	}
	d, err := cat.DefaultOf("port")
	must(t, err)
	eq(t, d, Default{int64(22), "", "fixed"})
	d, _ = cat.DefaultOf("domain")
	eq(t, d, Default{"my_thing", "Read from the integration's manifest.json", "worked_out"})
	d, _ = cat.DefaultOf("host")
	eq(t, d.How, "required")
	must(t, cat.SaveValue("port", 2222))
	eq(t, st.computer.Data, map[string]any{"tour": map[string]any{"host": "nas.local", "port": int64(2222)}})
	must(t, cat.SaveValue("port", nil))
	must(t, cat.SaveValue("host", ""))
	v, err := cat.Held("port", "22")
	must(t, err)
	eq(t, v, "22")
	eq(t, len(st.computer.Data), 0)
	for _, c := range []struct {
		id    string
		value any
		why   string
	}{{"port", 70000, "a port"}, {"host", "-oProxyCommand=x", "doesn't look like a host"}, {"mode", "windows", "one of nas, ha"},
		{"host", 7, "text"}, {"nothing", 1, "no setting 'nothing'"}} {
		failsWith(t, cat.SaveValue(c.id, c.value), c.why)
	}
	shown, err := cat.Shown("domain")
	must(t, err)
	eq(t, shown, false)
	must(t, cat.SaveValue("mode", "ha"))
	shown, _ = cat.Shown("domain")
	eq(t, shown, true)
	shown, _ = cat.Shown("port") // (a sub-setting: shown when its parent is)
	eq(t, shown, true)
	file, parts, err := cat.WhereSaved("mode")
	must(t, err)
	eq(t, file, "project")
	eq(t, parts, []string{"tour", "formula"})
	s, err := cat.SettingOf("mode")
	must(t, err)
	eq(t, s["demo_stricter"], "fact")
	choices, err := cat.ChoicesOf("mode")
	must(t, err)
	eq(t, choices, []any{[]any{"nas", "TrueNAS app"}, []any{"ha", "Home Assistant"}})
	choices, err = cat.ChoicesOf("host")
	must(t, err)
	if choices != nil {
		t.Fatal(choices)
	}
	// what can't be answered is said
	cat.stores = map[string]Store{}
	_, err = cat.ValueOf("host")
	failsWith(t, err, "the app hasn't said where the values of 'computer' are kept")
	cat.answers = map[string]AnswerFunc{}
	_, err = cat.DefaultOf("domain")
	failsWith(t, err, "the app doesn't answer 'manifest_domain'")
	for _, fn := range []func() error{
		func() error { _, err := cat.ChoicesOf("mode"); return err },
		func() error { _, err := cat.Held("mode", "nas"); return err },
		func() error { _, err := cat.Shown("domain"); return err },
		func() error { _, err := cat.MembersOf("servers"); return err },
		func() error { _, err := cat.State("host"); return err },
		func() error { _, err := cat.Page(nil); return err },
		func() error { _, err := cat.Unknown("project", nil); return err },
	} {
		if fn() == nil {
			t.Fatal("no error where nothing is answered")
		}
	}
	for _, fn := range []func() error{
		func() error { _, err := cat.SettingOf("nothing"); return err },
		func() error { _, _, err := cat.WhereSaved("nothing"); return err },
		func() error { _, err := cat.SavedValue("nothing"); return err },
		func() error { _, err := cat.DefaultOf("nothing"); return err },
		func() error { _, err := cat.ValueOf("nothing"); return err },
		func() error { _, err := cat.ChoicesOf("nothing"); return err },
		func() error { _, err := cat.Held("nothing", 1); return err },
		func() error { _, err := cat.Shown("nothing"); return err },
		func() error { _, err := cat.SavedSays("nothing", false); return err },
		func() error { _, err := cat.State("nothing"); return err },
		func() error { _, err := cat.MembersOf("nothing"); return err },
	} {
		failsWith(t, fn(), "no (setting|set) 'nothing' in sample.toml")
	}
}

func TestAValueIsHeldToItsKind(t *testing.T) {
	s := func(kv ...any) map[string]any {
		out := map[string]any{"label": "It"}
		for i := 0; i < len(kv); i += 2 {
			out[kv[i].(string)] = kv[i+1]
		}
		return out
	}
	for _, c := range []struct {
		s     map[string]any
		value any
		want  any
		why   string
	}{
		{s("kind", "switch"), true, true, ""},
		{s("kind", "switch"), "yes", nil, "on or off"},
		{s("kind", "choice"), "x", "x", ""},
		{s("kind", "number", "min", 1, "max", 9), 3.5, 3.5, ""},
		{s("kind", "slider", "min", 1, "max", 9), 10, nil, "from 1 to 9"},
		{s("kind", "number", "min", 1.5, "max", 9), true, nil, "a number"},
		{s("kind", "list"), "one", []any{"one"}, ""},
		{s("kind", "list", "type", "glob"), []string{"a/**", "b"}, []any{"a/**", "b"}, ""},
		{s("kind", "lines"), "one", nil, "a list"},
		{s("kind", "lines"), []any{"one\ntwo"}, []any{"one\ntwo"}, ""},
		{s("kind", "list"), []any{"one\ntwo"}, nil, "a line can't hold"},
		{s("kind", "list"), []any{1}, nil, "a line can't hold"},
		{s("kind", "text", "type", "port"), int64(22), int64(22), ""},
		{s("kind", "text", "type", "port"), 0, nil, "a port"},
		{s("kind", "text", "type", "port"), "0x1", nil, "a port"},
		{s("kind", "text", "type", "url"), "ftp://x", nil, "doesn't look like a url"},
		{s("kind", "text", "pattern", "[a-z]+", "pattern_says", "small letters"), "ABC", nil, "small letters"},
		{s("kind", "text", "pattern", "[a-z]+"), "ABC", nil, "isn't what's wanted here"},
		{s("kind", "text", "pattern", "[a-z]+"), "abc", "abc", ""},
		{s("kind", "secret"), "s3cret", "s3cret", ""},
		{map[string]any{"kind": "text"}, 1, nil, "^x: text"},
	} {
		got, err := held("x", c.s, c.value, nil, false)
		if c.why == "" {
			must(t, err)
			eq(t, got, c.want)
		} else {
			failsWith(t, err, c.why)
		}
	}
	_, err := held("x", s("kind", "choice"), "z", []any{"a", int64(2), 1.5, nil, true}, true)
	failsWith(t, err, "one of a, 2, 1.5, None, True")
}

func TestASettingInASetHasAValueForEachMember(t *testing.T) {
	cat, st := sample(t, nil, nil)
	must(t, cat.SaveValue("server_url", "https://nas1:32400", "nas1"))
	must(t, cat.SaveValue("server_limit", 80, "nas1", "push"))
	eq(t, st.project.Data, map[string]any{"servers": map[string]any{"nas1": map[string]any{"url": "https://nas1:32400", "push": map[string]any{"limit": 80}}}})
	v, _ := cat.ValueOf("server_limit", "nas1", "push")
	eq(t, v, 80)
	v, _ = cat.ValueOf("server_limit", "nas1", "merge")
	eq(t, v, 50)
	v, _ = cat.SavedValue("server_url", "nas2")
	eq(t, v, nil)
	must(t, cat.SaveValue("server_url", "https://every.one", "*"))
	v, _ = cat.ValueOf("server_url", "nas2")
	eq(t, v, "https://every.one")
	d, _ := cat.DefaultOf("server_url", "nas2")
	eq(t, d, Default{"https://every.one", "every server's", "shared"})
	d, _ = cat.DefaultOf("server_url", "*")
	eq(t, d.How, "required")
	v, _ = cat.ValueOf("server_url") // (no member, in a shared set: the shared one)
	eq(t, v, "https://every.one")
	_, err := cat.ValueOf("server_limit", "nas1")
	failsWith(t, err, "one for each of servers and gates")
	_, err = cat.ValueOf("host", "nas1")
	failsWith(t, err, `host: it's one for each of nothing: asked for with \['nas1'\]`)
	_, err = cat.DefaultOf("server_limit", "nas1")
	failsWith(t, err, "asked for with")
	listed, err := cat.MembersOf("servers")
	must(t, err)
	eq(t, listed, []Member{{"nas1", "The NAS"}, {"nas2", "nas2"}})
	says, err := cat.SavedSays("server_limit", false)
	must(t, err)
	eq(t, says, map[string]string{"key": "servers.<server>.<gate>.limit", "file": "app.toml"})
	cat.Answer("servers", func([]string) any {
		return []any{Member{ID: "a"}, map[string]string{"id": "b"}, map[string]string{"id": "c", "name": "C"}, map[string]any{"id": "d", "name": ""}}
	})
	listed, _ = cat.MembersOf("servers")
	eq(t, listed, []Member{{"a", "a"}, {"b", "b"}, {"c", "C"}, {"d", "d"}})
}

func TestASetsSharedValueMayBeKeptApartAndASettingOfItInAFileOfItsOwn(t *testing.T) {
	data := sampleData(t)
	data["sets"].(map[string]any)["servers"].(map[string]any)["shared_saved"] = map[string]any{"key": "every"}
	setting(data, "server_url")["saved"] = map[string]any{"file": "computer", "key": "url"}
	cat, err := New(data, "sample.toml", nil)
	must(t, err)
	project := cat.Store("project", NewMemoryStore(nil, "app.toml")).(*MemoryStore)
	computer := cat.Store("computer", NewMemoryStore(nil, "app.local.toml")).(*MemoryStore)
	for name, says := range map[string]any{"servers": []string{"nas1", "nas2"}, "gates": []string{"push"}, "manifest_domain": "my_thing", "formulas": [][]string{{"nas", "TrueNAS app"}}} {
		cat.Answer(name, func([]string) any { return says })
	}
	must(t, cat.SaveValue("server_url", "https://every.one", "*"))
	must(t, cat.SaveValue("server_url", "https://nas1:32400", "nas1"))
	must(t, cat.SaveValue("token", "s3cret", "*"))
	must(t, cat.SaveValue("server_limit", 9, "*", "push"))
	eq(t, computer.Data, map[string]any{"every": map[string]any{"url": "https://every.one"}, "servers": map[string]any{"nas1": map[string]any{"url": "https://nas1:32400"}}})
	eq(t, project.Data, map[string]any{"every": map[string]any{"token": "s3cret", "push": map[string]any{"limit": 9}}})
	d, _ := cat.DefaultOf("server_url", "nas2")
	eq(t, d, Default{"https://every.one", "every server's", "shared"})
	for _, c := range [][2]any{{[]string{"nas1"}, "https://nas1:32400"}, {[]string{"nas2"}, "https://every.one"}, {[]string{}, "https://every.one"}} {
		v, _ := cat.ValueOf("server_url", c[0].([]string)...)
		eq(t, v, c[1])
	}
	says, _ := cat.SavedSays("server_url", false)
	eq(t, says, map[string]string{"key": "servers.<server>.url", "file": "app.local.toml"})
	says, _ = cat.SavedSays("server_url", true)
	eq(t, says, map[string]string{"key": "every.url", "file": "app.local.toml"})
	says, _ = cat.SavedSays("token", true)
	eq(t, says, map[string]string{"key": "every.token", "file": "app.toml"})
	page := toJSON(t, cat, nil)
	eq(t, page["settings"].(map[string]any)["server_url"].(map[string]any)["saved_says_shared"], map[string]any{"key": "every.url", "file": "app.local.toml"})
	eq(t, page["states"].(map[string]any)["server_url|nas2"].(map[string]any)["default"].(map[string]any)["how"], "shared")
	project.Data["every"].(map[string]any)["colour"] = "red"
	project.Data["servers"] = map[string]any{"nas1": map[string]any{"token": "x", "merge": 1}}
	got, err := cat.Unknown("project", nil)
	must(t, err)
	eq(t, got, []string{"every.colour", "servers.nas1.merge"})
	for _, c := range []struct {
		change func(d map[string]any)
		why    string
	}{
		{func(d map[string]any) {
			d["sets"].(map[string]any)["gates"].(map[string]any)["shared_saved"] = map[string]any{"key": "all"}
		}, `\[sets.gates\] shared_saved: only a set that's shared`},
		{func(d map[string]any) {
			delete(d["sets"].(map[string]any)["servers"].(map[string]any)["saved"].(map[string]any), "file")
		}, `\[settings.token\] saved.file: neither it nor its set says`},
		{func(d map[string]any) { setting(d, "token")["saved"].(map[string]any)["file"] = "" }, `\[settings.token\] saved.file: which of the app's files`},
		{func(d map[string]any) { d["sets"].(map[string]any)["servers"].(map[string]any)["cascade"] = true }, `\[sets.servers\]: a set is shared .* or cascade .*, not both`},
	} {
		matches(t, lines(broken(t, c.change), ""), c.why)
	}
}

func TestASetThatCascadesGivesEachMemberTheValueOfTheOneBeforeIt(t *testing.T) {
	data := sampleData(t)
	data["sets"].(map[string]any)["gates"].(map[string]any)["cascade"] = true
	cat, err := New(data, "sample.toml", nil)
	must(t, err)
	project := cat.Store("project", NewMemoryStore(nil, "app.toml")).(*MemoryStore)
	cat.Store("computer", NewMemoryStore(nil, "app.local.toml"))
	cat.Answer("gates", func([]string) any { return []any{map[string]any{"id": "dev", "name": "Dev"}, "push", "merge"} })
	for name, says := range map[string]any{"servers": []string{"nas1"}, "manifest_domain": "my_thing", "formulas": [][]string{{"nas", "TrueNAS app"}}} {
		cat.Answer(name, func([]string) any { return says })
	}
	values := func() []any {
		var out []any
		for _, g := range []string{"dev", "push", "merge"} {
			v, err := cat.ValueOf("server_limit", "nas1", g)
			must(t, err)
			out = append(out, v)
		}
		return out
	}
	eq(t, values(), []any{50, 50, 50})
	d, _ := cat.DefaultOf("server_limit", "nas1", "dev")
	eq(t, d, Default{int64(50), "", "fixed"})
	must(t, cat.SaveValue("server_limit", 80, "nas1", "dev"))
	eq(t, values(), []any{80, 80, 80})
	d, _ = cat.DefaultOf("server_limit", "nas1", "push")
	eq(t, d, Default{80, "Dev's", "cascade"})
	must(t, cat.SaveValue("server_limit", 90, "nas1", "push"))
	d, _ = cat.DefaultOf("server_limit", "nas1", "merge")
	eq(t, d, Default{90, "push's", "cascade"})
	must(t, cat.SaveValue("server_limit", 90, "nas1", "merge")) // (what it takes from push anyway: nothing of its own is kept)
	eq(t, project.Data, map[string]any{"servers": map[string]any{"nas1": map[string]any{"dev": map[string]any{"limit": 80}, "push": map[string]any{"limit": 90}}}})
	must(t, cat.SaveValue("server_limit", 80, "nas1", "push")) // (now the same as Dev's: its own is taken away)
	eq(t, project.Data, map[string]any{"servers": map[string]any{"nas1": map[string]any{"dev": map[string]any{"limit": 80}}}})
	eq(t, values(), []any{80, 80, 80})
	page := toJSON(t, cat, map[string][]string{"servers": {"nas1"}})
	eq(t, page["sets"].(map[string]any)["gates"], map[string]any{"title": "Gates", "member": "gate", "shared": false, "cascade": true, "list": false})
	cat.Answer("gates", func([]string) any { return 7 }) // (an answer that isn't a list: no members)
	_, err = cat.DefaultOf("server_limit", "nas1", "push")
	must(t, err)
}

func toJSON(t *testing.T, cat *Catalog, members map[string][]string) map[string]any {
	t.Helper()
	page, err := cat.Page(members)
	must(t, err)
	b, err := json.Marshal(page)
	must(t, err)
	out := map[string]any{}
	must(t, json.Unmarshal(b, &out))
	return out
}

func TestThePageIsGivenEverySettingAndNeverASecret(t *testing.T) {
	cat, _ := sample(t, map[string]any{"servers": map[string]any{"nas1": map[string]any{"token": "s3cret"}}}, nil)
	page, err := cat.Page(nil)
	must(t, err)
	b, err := json.Marshal(page)
	must(t, err)
	if strings.Contains(string(b), "s3cret") {
		t.Fatal("a secret went to the page")
	}
	// (in the file's order, as Python's dicts are)
	matches(t, string(b), `^\{"format":1,"groups":\{"net":.*"server":.*"settings":\{"host":.*"port":.*"domain":.*"mode":.*"server_url":.*"server_limit":.*"token":`)
	got := toJSON(t, cat, nil)
	defs, states := got["settings"].(map[string]any), got["states"].(map[string]any)
	eq(t, defs["host"].(map[string]any)["subs"], []any{"port"})
	eq(t, defs["port"].(map[string]any)["group"], "net")
	eq(t, defs["host"].(map[string]any)["saved_says"], map[string]any{"key": "tour.host", "file": "app.local.toml"})
	eq(t, states["mode"].(map[string]any)["choices"], []any{[]any{"nas", "TrueNAS app"}, []any{"ha", "Home Assistant"}})
	var urls []string
	for k := range states {
		if strings.HasPrefix(k, "server_url") {
			urls = append(urls, k)
		}
	}
	eq(t, len(urls), 3)
	if _, ok := states["server_limit|nas2|merge"]; !ok {
		t.Fatal(states)
	}
	eq(t, states["token|nas1"], map[string]any{"default": map[string]any{"value": nil, "says": "", "how": "required"}, "shown": true, "set": true})
	if _, err := (&Ordered{Keys: []string{"x"}, Values: map[string]any{"x": func() {}}}).MarshalJSON(); err == nil {
		t.Fatal("a value JSON can't hold was written")
	}
}

func TestSettingsThatAreDataAreAddedAndHeldToTheFormat(t *testing.T) {
	cat, _ := sample(t, nil, nil)
	app := func() map[string]any {
		return map[string]any{"app_name": map[string]any{"label": "app", "info": map[string]any{"does": "Names the app on the NAS that a push restarts once the files are copied, so the new code is what runs.", "when": "Give it when the project is first set up to push, and again if the app is ever installed afresh under another name.", "config": "The name the Apps page lists it under, in small letters."}, "kind": "text", "group": "net", "saved": map[string]any{"file": "project", "key": "tour.app"}, "required": true}}
	}
	must(t, cat.Add(app(), nil, nil))
	d, _ := cat.DefaultOf("app_name")
	eq(t, d.How, "required")
	eq(t, cat.Added["app_name"], true)
	eq(t, cat.IDs()[len(cat.IDs())-1], "app_name")
	bad := app()
	delete(bad["app_name"].(map[string]any), "info")
	other, _ := sample(t, nil, nil)
	failsWith(t, other.Add(bad, nil, nil), `\[settings.app_name\] info: missing`)
}

func TestASavedValueNoSettingHasIsFound(t *testing.T) {
	cat, _ := sample(t, map[string]any{"tour": map[string]any{"formula": "ha", "colour": "red"}, "servers": map[string]any{"nas1": map[string]any{"url": "https://x", "push": map[string]any{"limit": 5, "speed": 1}, "merge": 7}}, "other": map[string]any{"x": 1}}, nil)
	got, err := cat.Unknown("project", nil)
	must(t, err)
	eq(t, got, []string{"other", "servers.nas1.merge", "servers.nas1.push.speed", "tour.colour"})
	got, _ = cat.Unknown("project", []string{"tour"})
	eq(t, got, []string{"tour.colour"})
}

func TestAFileIsLoadedInItsOwnOrder(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "settings.toml")
	src := strings.Replace(string(sampleSrc(t)), "[format]", "[settings]\nzz = { label = \"z\", kind = \"switch\", group = \"net\", saved = { file = \"project\", key = \"zz\" }, default = false, info = { does = \"Stands in for a setting said inline, in the settings table itself, so its order is still the file's own.\", when = \"Never by itself: it is here so that a setting said before the others comes first, as the file says it.\" } }\n\n[format]", 1)
	must(t, os.WriteFile(path, []byte(src), 0o644))
	cat, err := Load(path)
	must(t, err)
	eq(t, cat.Where, "settings.toml")
	eq(t, cat.IDs()[0], "zz")
	_, err = Load(filepath.Join(dir, "none.toml"))
	failsWith(t, err, "none.toml")
	must(t, os.WriteFile(path, []byte("[format\n"), 0o644))
	_, err = Load(path)
	failsWith(t, err, "settings.toml: ")
	must(t, os.WriteFile(path, []byte("[format]\nversion = 2\n"), 0o644))
	_, err = Load(path)
	failsWith(t, err, `settings.toml isn't in the settings format:\n  \[format\] version = 1: missing`)
}

func TestAJSONFileKeepsWhatIsSaved(t *testing.T) {
	dir := t.TempDir()
	cat, _ := sample(t, nil, nil)
	store := cat.Store("project", NewJSONStore(filepath.Join(dir, "values.json"), "")).(*JSONStore)
	eq(t, store.Name(), "values.json")
	got, err := store.Read()
	must(t, err)
	eq(t, len(got), 0)
	must(t, cat.SaveValue("server_limit", 9, "nas1", "push"))
	must(t, cat.SaveValue("mode", "ha"))
	got, _ = store.Read()
	eq(t, got, map[string]any{"servers": map[string]any{"nas1": map[string]any{"push": map[string]any{"limit": int64(9)}}}, "tour": map[string]any{"formula": "ha"}})
	must(t, cat.SaveValue("server_limit", nil, "nas1", "push"))
	got, _ = store.Read()
	eq(t, got, map[string]any{"tour": map[string]any{"formula": "ha"}})
	must(t, os.WriteFile(store.Path, []byte(`{"a": 1.5, "b": [1, {"c": 2}]}`), 0o644))
	got, _ = store.Read()
	eq(t, got, map[string]any{"a": 1.5, "b": []any{int64(1), map[string]any{"c": int64(2)}}})
	must(t, os.WriteFile(store.Path, []byte(`[1]`), 0o644))
	got, _ = store.Read()
	eq(t, len(got), 0)
	must(t, os.WriteFile(store.Path, []byte(`{`), 0o644))
	_, err = store.Read()
	failsWith(t, err, "values.json")
	failsWith(t, cat.SaveValue("mode", "ha"), "values.json")
	_, err = NewJSONStore(dir, "x").Read() // (a folder: can't be read as a file)
	if err == nil {
		t.Fatal("a folder was read as a file")
	}
	failsWith(t, NewJSONStore(filepath.Join(dir, "no", "such", "values.json"), "").Write([]string{"a"}, 1), "no such file")
	must(t, os.WriteFile(store.Path, []byte(`{"a": 1}`), 0o644))
	failsWith(t, store.Write([]string{"b"}, func() {}), "unsupported type")
}

func TestATomlFileIsReadAndWrittenOnlyByTheApp(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "x.toml")
	cat, _ := sample(t, nil, nil)
	store := cat.Store("computer", NewTomlStore(path, "", nil)).(*TomlStore)
	eq(t, store.Name(), "x.toml")
	got, err := store.Read()
	must(t, err)
	eq(t, len(got), 0)
	failsWith(t, cat.SaveValue("host", "nas.local"), "x.toml is only read here")
	must(t, os.WriteFile(path, []byte("[tour]\nhost = \"nas.local\"\n"), 0o644))
	v, err := cat.ValueOf("host")
	must(t, err)
	eq(t, v, "nas.local")
	var wrote []any
	store.Writer = func(parts []string, value any) error { wrote = append(wrote, parts, value); return nil }
	must(t, cat.SaveValue("host", "nas2.local"))
	eq(t, wrote, []any{[]string{"tour", "host"}, "nas2.local"})
	must(t, os.WriteFile(path, []byte("[tour\n"), 0o644))
	_, err = store.Read()
	failsWith(t, err, "x.toml")
	_, err = NewTomlStore(dir, "", nil).Read()
	if err == nil {
		t.Fatal("a folder was read as a file")
	}
}

func TestPutTakesEmptyTablesAwayWithTheirLastValue(t *testing.T) {
	data := map[string]any{"a": map[string]any{"b": map[string]any{"c": 1}, "d": 2}}
	Put(data, []string{"x", "y"}, nil) // (nothing there: nothing to take away)
	Put(data, []string{"a", "b", "c"}, nil)
	eq(t, data, map[string]any{"a": map[string]any{"d": 2}})
	Put(data, []string{"a", "d"}, nil)
	eq(t, len(data), 0)
	m := NewMemoryStore(nil, "")
	eq(t, m.Name(), "memory")
}

func TestValuesAreComparedAndSaidAsPythonDoes(t *testing.T) {
	for _, c := range []struct {
		a, b any
		same bool
	}{{1, 1.0, true}, {int64(1), uint8(1), true}, {1, "1", false}, {"1", 1, false}, {[]string{"a"}, []any{"a"}, true}, {[]any{"a"}, []any{"a", "b"}, false},
		{[]any{"a"}, "a", false}, {[]any{1}, []any{2}, false}, {map[string]any{"a": 1}, map[string]any{"a": 1.0}, true}, {map[string]any{"a": 1}, map[string]any{"b": 1}, false},
		{map[string]any{"a": 1}, 1, false}, {nil, nil, true}, {true, true, true}, {int8(1), int16(1), true}, {int32(1), uint(1), true}, {uint16(1), uint32(1), true},
		{uint64(1), float32(1), true}} {
		if same(c.a, c.b) != c.same {
			t.Fatalf("same(%#v, %#v) != %v", c.a, c.b, c.same)
		}
	}
	for v, want := range map[any]string{nil: "None", true: "True", false: "False", 1.5: "1.5", 2.0: "2.0", int64(3): "3", "x": "x", 1e20: "1e+20"} {
		eq(t, pyStr(v), want)
	}
	eq(t, pyStr(struct{}{}), "{}")
	eq(t, pyRepr([]any{"a", 1}), "['a', 1]")
	for _, v := range []any{nil, false, 0, "", map[string]any{}, []any{}} {
		if truthy(v) {
			t.Fatalf("%#v is true", v)
		}
	}
	for _, v := range []any{true, 1, "x", map[string]any{"a": 1}, []any{1}, struct{}{}} {
		if !truthy(v) {
			t.Fatalf("%#v is false", v)
		}
	}
	if n, ok := isInt(uint(3)); !ok || n != 3 {
		t.Fatal(n)
	}
	if _, ok := isInt(1.0); ok {
		t.Fatal("a float is an int")
	}
	if _, ok := asList(nil); ok {
		t.Fatal("nil is a list")
	}
	eq(t, each(map[string]any{"each": 7}), []string{})
	eq(t, (&Error{"x"}).Error(), "x")
}

type brokenStore struct{}

func (brokenStore) Read() (map[string]any, error) { return nil, errors.New("the disk is gone") }
func (brokenStore) Write([]string, any) error     { return errors.New("the disk is gone") }
func (brokenStore) Name() string                  { return "" }

func TestWhatCantBeReadIsSaid(t *testing.T) {
	cat, _ := sample(t, nil, nil)
	cat.Store("project", brokenStore{})
	for _, fn := range []func() error{
		func() error { _, err := cat.ValueOf("mode"); return err },
		func() error { _, err := cat.DefaultOf("server_url", "nas1"); return err },
		func() error { _, err := cat.State("mode"); return err },
		func() error { _, err := cat.State("domain"); return err },
		func() error { return cat.SaveValue("server_url", "https://x", "nas1") },
		func() error { return cat.SaveValue("mode", "ha") },
		func() error { _, err := cat.Page(nil); return err },
		func() error { _, err := cat.Unknown("project", nil); return err },
	} {
		failsWith(t, fn(), "the disk is gone")
	}
	says, _ := cat.SavedSays("mode", false)
	eq(t, says["file"], "project") // (a store with no name: the file's own name for it)
	// a set's members, or a choice's choices, that can't be answered
	cat, _ = sample(t, nil, nil)
	delete(cat.answers, "gates")
	for _, fn := range []func() error{
		func() error { _, err := cat.Page(nil); return err },
		func() error { _, err := cat.Page(map[string][]string{"servers": {"nas1"}}); return err },
	} {
		failsWith(t, fn(), "the app doesn't answer 'gates'")
	}
	data := sampleData(t)
	data["sets"].(map[string]any)["gates"].(map[string]any)["cascade"] = true
	cat, _ = New(data, "sample.toml", nil)
	cat.Store("project", NewMemoryStore(nil, ""))
	_, err := cat.DefaultOf("server_limit", "nas1", "push")
	failsWith(t, err, "the app doesn't answer 'gates'")
	failsWith(t, cat.SaveValue("server_limit", 3, "nas1", "push"), "the app doesn't answer 'gates'")
	cat.Answer("gates", func([]string) any { return []string{"dev", "push"} })
	cat.Answer("servers", func([]string) any { return []string{"nas1"} })
	cat.stores = map[string]Store{"project": brokenStore{}}
	_, err = cat.DefaultOf("server_limit", "nas1", "push") // (the member before it: its value can't be read)
	failsWith(t, err, "the disk is gone")
	cat.stores = map[string]Store{}
	failsWith(t, cat.SaveValue("server_limit", nil, "nas1", "push"), "the app hasn't said where")
	cat, _ = sample(t, nil, nil)
	delete(cat.answers, "formulas")
	_, err = cat.State("mode")
	failsWith(t, err, "the app doesn't answer 'formulas'")
	_, err = cat.Page(nil)
	failsWith(t, err, "the app doesn't answer 'formulas'")
	cat, _ = sample(t, nil, nil)
	delete(cat.answers, "manifest_domain")
	_, err = cat.State("domain")
	failsWith(t, err, "the app doesn't answer 'manifest_domain'")
	cat, _ = sample(t, nil, nil)
	cat.Settings["host"]["shown_when"] = map[string]any{"setting": "gone", "is": 1}
	_, err = cat.Shown("host")
	failsWith(t, err, "no setting 'gone'")
	_, err = cat.State("host")
	failsWith(t, err, "no setting 'gone'")
	delete(cat.Settings["host"], "shown_when")
	cat.Settings["mode"]["each"] = "nothing" // (a set whose members can't be answered)
	cat.Sets["nothing"] = map[string]any{"saved": map[string]any{"key": "x"}, "member": "x"}
	_, err = cat.SavedSays("mode", true)
	must(t, err)
	_, err = cat.Page(nil)
	failsWith(t, err, "one for each of nothing")
}

func TestShownWhenAFeatureIsThereOrAMembersOwnValueSaysSo(t *testing.T) {
	cat, st := sample(t, map[string]any{"tour": "not a table"}, nil)
	v, err := cat.ValueOf("mode") // (saved under a value that isn't a table: nothing saved)
	must(t, err)
	eq(t, v, "nas")
	extra := map[string]any{
		"ha_only": map[string]any{"label": "Only on HA", "kind": "choice", "choices": []any{[]any{"a", "A"}, []any{"b", "B"}}, "group": "net",
			"saved": map[string]any{"file": "project", "key": "ha.only"}, "default": "a", "shown_when": map[string]any{"feature": "home_assistant"},
			"info": map[string]any{"does": "Picks which of two ways the integration is copied in, only where the remote machine runs Home Assistant.", "when": "Leave it as it is unless a copy of the integration ends up in the wrong folder after a push, which changes nothing else.", "config": "A puts it under custom_components; B keeps the folder it came from."}},
		"server_note": map[string]any{"label": "Note", "kind": "text", "group": "server", "each": "servers", "saved": map[string]any{"key": "note"}, "default": "x",
			"shown_when": map[string]any{"setting": "server_url", "is": "https://nas1"},
			"info":       map[string]any{"does": "Keeps a few words about this one server beside its address, shown wherever its row is drawn on the page.", "when": "Give it to remember why a server is set up the way it is, such as who looks after it or what disks it has.", "config": "Any words that fit on one line, without line breaks in them."}},
	}
	must(t, cat.Add(extra, nil, nil))
	choices, err := cat.ChoicesOf("ha_only")
	must(t, err)
	eq(t, choices, []any{[]any{"a", "A"}, []any{"b", "B"}})
	feature := false
	cat.Answer("home_assistant", func([]string) any { return feature })
	shown, err := cat.Shown("ha_only")
	must(t, err)
	eq(t, shown, false)
	feature = true
	shown, _ = cat.Shown("ha_only")
	eq(t, shown, true)
	must(t, cat.SaveValue("server_url", "https://nas1", "nas1"))
	shown, _ = cat.Shown("server_note", "nas1")
	eq(t, shown, true)
	shown, _ = cat.Shown("server_note", "nas2")
	eq(t, shown, false)
	failsWith(t, cat.SaveValue("ha_only", "c"), "one of a, b")
	eq(t, st.project.Data["servers"], map[string]any{"nas1": map[string]any{"url": "https://nas1"}})
	eq(t, lines(broken(t, func(d map[string]any) { setting(d, "host")["saved"] = map[string]any{"key": "tour.host"} }), ""), "[settings.host] saved.file: which of the app's files (project, computer, or a name it gives one)")
	eq(t, typeOK("nothing", 1), false)
	eq(t, same(map[string]any{"a": 1}, []any{1}), false)
	eq(t, empty(7), false)
}

// listData is the sample with a set kept as a list ([[watch]]) and a pairs setting.
func listData(t *testing.T) map[string]any {
	t.Helper()
	data := sampleData(t)
	data["sets"].(map[string]any)["watches"] = map[string]any{"title": "Watches", "member": "watch", "members_from": "watches", "list": true,
		"saved": map[string]any{"file": "project", "key": "watch"}}
	info := map[string]any{"does": "Names the folder of one project this machine keeps an eye on, so its changes are noticed while nobody is there.",
		"when":   "Add one for each project that should be checked by itself, and take it away once the project is no longer worked on.",
		"config": "The path to the project's folder, starting at your home folder or absolute."}
	settingsOf(data)["watch_project"] = map[string]any{"label": "Project", "kind": "text", "group": "server", "each": "watches",
		"saved": map[string]any{"key": "project"}, "required": true, "info": info}
	envInfo := map[string]any{"does": info["does"], "when": info["when"], "config": "Each variable's name, with the text it's given, such as DEBUG with 1."}
	settingsOf(data)["env"] = map[string]any{"label": "Environment", "kind": "pairs", "group": "server", "default": map[string]any{},
		"saved": map[string]any{"file": "project", "key": "deploy.env"}, "info": envInfo}
	return data
}

// A set kept as a list ([[watch]]): each member is an item, by its place from 1, read and written there, and an item's
// key no setting has is found; it has no shared value. A pairs setting: names, each with its text.
func TestAListSetsMembersAreItsItemsAndPairsAreNamesWithTheirText(t *testing.T) {
	data := listData(t)
	eq(t, len(Problems(data)), 0)
	cat, err := New(data, "sample.toml", nil)
	must(t, err)
	project := NewMemoryStore(map[string]any{"watch": []any{map[string]any{"project": "~/a"}, map[string]any{"project": "~/b", "every": "1m"}}}, "app.toml")
	cat.Store("project", project)
	cat.Answer("watches", func([]string) any { return []string{"1", "2"} })
	cat.Store("computer", NewMemoryStore(nil, "app.local.toml"))
	cat.Answer("servers", func([]string) any { return []string{} })
	cat.Answer("gates", func([]string) any { return []string{} })
	cat.Answer("formulas", func([]string) any { return [][]string{} })
	cat.Answer("manifest_domain", func([]string) any { return nil })
	if pg, err := cat.Page(nil); err != nil {
		t.Fatal(err)
	} else if raw, _ := json.Marshal(pg); !strings.Contains(string(raw), `"watches":{"cascade":false,"list":true,"member":"watch","shared":false,"title":"Watches"}`) {
		t.Errorf("the drawer isn't told it's a list: %s", raw)
	}
	a, _ := cat.ValueOf("watch_project", "1")
	b, _ := cat.ValueOf("watch_project", "2")
	c, _ := cat.SavedValue("watch_project", "3")
	eq(t, []any{a, b, c}, []any{"~/a", "~/b", nil})
	must(t, cat.SaveValue("watch_project", "~/c", "3"))
	must(t, cat.SaveValue("watch_project", "~/z", "1"))
	eq(t, project.Data["watch"], []any{map[string]any{"project": "~/z"}, map[string]any{"project": "~/b", "every": "1m"}, map[string]any{"project": "~/c"}})
	must(t, cat.SaveValue("watch_project", nil, "1"))
	eq(t, project.Data["watch"].([]any)[0], map[string]any{}) // (an item keeps its place: the members after it are still themselves)
	must(t, cat.SaveValue("watch_project", nil, "9"))         // (nothing there to take away)
	unknown, err := cat.Unknown("project", []string{"watch"})
	must(t, err)
	eq(t, unknown, []string{"watch.#2.every"})
	says, err := cat.SavedSays("watch_project", false)
	must(t, err)
	eq(t, says, map[string]string{"key": "watch.<watch>.project", "file": "app.toml"})
	fresh := NewMemoryStore(nil, "x.toml")
	cat.Store("project", fresh)
	must(t, cat.SaveValue("watch_project", "~/d", "2"))
	eq(t, fresh.Data, map[string]any{"watch": []any{map[string]any{}, map[string]any{"project": "~/d"}}})
	must(t, cat.SaveValue("env", map[string]any{"DEBUG": "1", "APP_KEY": "x"}))
	eq(t, fresh.Data["deploy"], map[string]any{"env": map[string]any{"DEBUG": "1", "APP_KEY": "x"}})
	failsWith(t, cat.SaveValue("env", "DEBUG=1"), "a table of names")
	failsWith(t, cat.SaveValue("env", map[string]any{"BAD NAME": "1"}), "a name is")
	failsWith(t, cat.SaveValue("env", map[string]any{"A": 1}), "text")
	for _, bad := range []struct {
		change func(d map[string]any)
		why    string
	}{
		{func(d map[string]any) { d["sets"].(map[string]any)["watches"].(map[string]any)["shared"] = true }, `\[sets.watches\]: a list's members are its items`},
		{func(d map[string]any) {
			d["sets"].(map[string]any)["watches"].(map[string]any)["saved"].(map[string]any)["key"] = ""
		}, `\[sets.watches\] saved.key: where its list is kept`},
	} {
		d := listData(t)
		bad.change(d)
		var lines []string
		for _, p := range Problems(d) {
			lines = append(lines, p.Line)
		}
		matches(t, strings.Join(lines, "\n"), bad.why)
	}
	// Put and atPath, by themselves: a list made where an item's place is next, a place past the list's end.
	data2 := map[string]any{}
	Put(data2, []string{"a", "#2"}, "x")
	eq(t, data2, map[string]any{"a": []any{map[string]any{}, "x"}})
	eq(t, atPath(data2, []string{"a", "#2"}), "x")
	eq(t, atPath(data2, []string{"a", "#3"}), nil)
	eq(t, atPath(data2, []string{"a", "#2", "b"}), nil)
	Put(data2, []string{"a", "#2", "b"}, nil) // (an item that isn't a table: nothing under it to take away)
	Put(data2, []string{"a", "#5", "b"}, nil)
	Put(data2, []string{"b", "c"}, nil)
	eq(t, data2, map[string]any{"a": []any{map[string]any{}, "x"}})
	Put(data2, []string{"s"}, "t")
	Put(data2, []string{"s", "u"}, nil)
	eq(t, data2["s"], "t")
}

// The drawer's script comes with the reader, for a Go app's page to serve.
func TestTheDrawerComesWithTheReader(t *testing.T) {
	if !strings.Contains(Drawer, "SettingsLib") || !strings.Contains(Drawer, `s.kind === "pairs"`) {
		t.Error("the drawer's script isn't settings.js")
	}
}
