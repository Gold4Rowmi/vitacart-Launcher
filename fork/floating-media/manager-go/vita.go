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

type Game struct {
	ID      string `json:"id"`
	Type    int    `json:"type"`
	Title   string `json:"title"`
	Icon    string `json:"icon"`
	Hidden  bool   `json:"hidden"`
	System  string `json:"system"`
	Kind    string `json:"kind"`
	Printed bool   `json:"printed"`
	DiscArt bool   `json:"discart"`
	Cover   string `json:"cover"`
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
