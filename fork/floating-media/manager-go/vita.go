package main

// Talking to the Vita through vitacompanion: FTP on 1337 and the command server on 1338.

import (
	"bytes"
	"fmt"
	"io"
	"net"
	"regexp"
	"sort"
	"strings"
	"time"

	"github.com/jlaffaye/ftp"
)

const titleID = "RFFLOAT01"
const printRoot = "ux0:/data/RetroFlowFloat/PRINT/COVERS"
const cacheRoot = "ux0:/data/RetroFlowFloat/CACHE"
const wallpaperRoot = "ux0:/data/RetroFlowFloat/WALLPAPER"

type System struct {
	Folder string
	Kind   string // card, umd, disc
	Suffix string // real disc art file suffix ("" for cards)
}

var systems = map[int]System{
	1: {"Sony - PlayStation Vita", "card", ""},
	2: {"Sony - PlayStation Portable", "umd", "_umd.png"},
	3: {"Sony - PlayStation", "disc", "_disc.png"},
}

const mediaTypesPath = "ux0:/data/RetroFlowFloat/media_types.lua"

var kindOf = map[int]string{1: "vita", 2: "psp", 3: "psx"}

// readMediaTypes: per-game "show as" overrides written for the Lua ({ ["ID"] = "vita", ... }).
func readMediaTypes(c *ftp.ServerConn) map[string]string {
	out := map[string]string{}
	data, err := ftpGet(c, mediaTypesPath)
	if err != nil {
		return out
	}
	re := regexp.MustCompile(`\["([A-Za-z0-9_]+)"\]\s*=\s*"(vita|psp|psx|psxcase)"`)
	for _, m := range re.FindAllStringSubmatch(string(data), -1) {
		out[m[1]] = m[2]
	}
	return out
}

func writeMediaTypes(c *ftp.ServerConn, m map[string]string) error {
	keys := make([]string, 0, len(m))
	for k := range m {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	var b strings.Builder
	b.WriteString("-- RetroFlow Float: per-game media type (written by the Art Manager)\nreturn {\n")
	for _, k := range keys {
		fmt.Fprintf(&b, "\t[%q] = %q,\n", k, m[k])
	}
	b.WriteString("}\n")
	return ftpPut(c, mediaTypesPath, []byte(b.String()))
}

type Game struct {
	ID       string `json:"id"`
	Type     int    `json:"type"`
	Title    string `json:"title"`
	Icon     string `json:"icon"`
	Hidden   bool   `json:"hidden"`
	System   string `json:"system"`
	Kind     string `json:"kind"`
	Override string `json:"override"`
	Printed  bool   `json:"printed"`
	DiscArt  bool   `json:"discart"`
	Cover    string `json:"cover"`
}

// One FTP session, reused and paced: vitacompanion's server is small and dislikes connection churn.
var (
	ftpConn *ftp.ServerConn
	ftpCfg  Config
	lastOp  time.Time
)

func getFTP(cfg Config) (*ftp.ServerConn, error) {
	if ftpConn != nil && (cfg.IP != ftpCfg.IP || cfg.FTPPort != ftpCfg.FTPPort) {
		ftpConn.Quit()
		ftpConn = nil
	}
	if ftpConn != nil {
		if err := ftpConn.NoOp(); err != nil {
			ftpConn.Quit()
			ftpConn = nil
		}
	}
	if ftpConn == nil {
		c, err := dial(cfg)
		if err != nil {
			return nil, err
		}
		ftpConn, ftpCfg = c, cfg
	}
	// pace transfers a little so the Vita never sees back-to-back commands
	if d := time.Since(lastOp); d < 60*time.Millisecond {
		time.Sleep(60*time.Millisecond - d)
	}
	lastOp = time.Now()
	// let the session go after a few idle seconds: vitacompanion serves one client at a time,
	// so a session held open blocks every other FTP tool (and the deploy script) on the Vita
	if idleTimer != nil {
		idleTimer.Stop()
	}
	idleTimer = time.AfterFunc(ftpIdle, func() {
		vitaMutex.Lock()
		defer vitaMutex.Unlock()
		if ftpConn != nil && time.Since(lastOp) >= ftpIdle {
			ftpConn.Quit()
			ftpConn = nil
		}
	})
	return ftpConn, nil
}

const ftpIdle = 5 * time.Second

var idleTimer *time.Timer

func dropFTP() {
	if ftpConn != nil {
		ftpConn.Quit()
		ftpConn = nil
	}
}

func dial(cfg Config) (*ftp.ServerConn, error) {
	c, err := ftp.Dial(fmt.Sprintf("%s:%d", cfg.IP, cfg.FTPPort), ftp.DialWithTimeout(15*time.Second))
	if err != nil {
		return nil, err
	}
	if err := c.Login("anonymous", "anonymous"); err != nil {
		c.Quit()
		return nil, err
	}
	return c, nil
}

func ftpList(c *ftp.ServerConn, path string) map[string]bool {
	names := map[string]bool{}
	entries, err := c.List(path)
	if err != nil {
		return names
	}
	for _, e := range entries {
		names[e.Name] = true
	}
	return names
}

func ftpGet(c *ftp.ServerConn, path string) ([]byte, error) {
	r, err := c.Retr(path)
	if err != nil {
		return nil, err
	}
	defer r.Close()
	return io.ReadAll(r)
}

// ftpPut creates the folders on the way (vitacompanion needs them made explicitly).
func ftpPut(c *ftp.ServerConn, path string, data []byte) error {
	if err := c.Stor(path, bytes.NewReader(data)); err == nil {
		return nil
	}
	parts := strings.Split(path, "/")
	for i := 2; i < len(parts); i++ {
		c.MakeDir(strings.Join(parts[:i], "/"))
	}
	return c.Stor(path, bytes.NewReader(data))
}

func vitaCmd(cfg Config, cmd string) string {
	conn, err := net.DialTimeout("tcp", fmt.Sprintf("%s:%d", cfg.IP, cfg.CmdPort), 5*time.Second)
	if err != nil {
		return err.Error()
	}
	defer conn.Close()
	fmt.Fprintf(conn, "%s\n", cmd)
	conn.SetReadDeadline(time.Now().Add(3 * time.Second))
	buf := make([]byte, 4096)
	n, _ := conn.Read(buf)
	return strings.TrimSpace(string(buf[:n]))
}

func portOpen(host string, port int) bool {
	conn, err := net.DialTimeout("tcp", fmt.Sprintf("%s:%d", host, port), 3*time.Second)
	if err != nil {
		return false
	}
	conn.Close()
	return true
}

var entryRe = regexp.MustCompile(`(?s)\{(.*?)\n\t\},`)

func field(entry, key string) string {
	re := regexp.MustCompile(`\["` + key + `"\] = ("?)([^,\n]*?)("?),`)
	m := re.FindStringSubmatch(entry)
	if m == nil {
		return ""
	}
	return m[2]
}

// parseCache reads one of RetroFlow Float's cache files (a Lua table dump).
func parseCache(txt string) []Game {
	var games []Game
	for _, m := range entryRe.FindAllStringSubmatch(txt, -1) {
		e := m[1]
		var t int
		switch field(e, "app_type") {
		case "1":
			t = 1
		case "2":
			t = 2
		case "3":
			t = 3
		default:
			continue
		}
		id := strings.ReplaceAll(field(e, "titleid"), "-", "")
		if id == "" {
			id = field(e, "name")
		}
		if id == "" {
			continue
		}
		title := field(e, "apptitle")
		if title == "" {
			title = field(e, "name")
		}
		games = append(games, Game{ID: id, Type: t, Title: title, Icon: field(e, "icon_path"), Hidden: field(e, "hidden") == "true"})
	}
	return games
}

func library(c *ftp.ServerConn) []Game {
	seen := map[string]bool{}
	var out []Game
	for _, db := range []string{"db_games.lua", "db_psp.lua", "db_psx.lua"} {
		data, err := ftpGet(c, cacheRoot+"/"+db)
		if err != nil {
			continue
		}
		for _, g := range parseCache(string(data)) {
			if !seen[g.ID] {
				seen[g.ID] = true
				out = append(out, g)
			}
		}
	}
	sort.SliceStable(out, func(i, j int) bool {
		if out[i].Type != out[j].Type {
			return out[i].Type < out[j].Type
		}
		return strings.ToLower(out[i].Title) < strings.ToLower(out[j].Title)
	})
	return out
}
