// RetroFlow Float Art Manager: one small executable, no install. Run it, a browser opens at
// http://localhost:8765. It talks to the Vita through vitacompanion (FTP 1337, commands 1338),
// lists the PS Vita / PSP / PS1 games from RetroFlow Float's own cache with their current
// art, and lets you drop a disc scan or cover on a game: the disc is found and cropped to a
// round label, optionally given the printed look, and uploaded to Float's private folder
// (ux0:/data/RetroFlowFloat/PRINT/COVERS/...). Normal RetroFlow is never touched.
package main

import (
	"bytes"
	"embed"
	"encoding/json"
	"fmt"
	"image"
	_ "image/gif"
	_ "image/jpeg"
	"image/png"
	"log"
	"math"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"runtime"
	"strconv"
	"strings"
	"sync"
	"time"

	_ "golang.org/x/image/webp"
)

//go:embed web/index.html
var web embed.FS

type Config struct {
	IP        string  `json:"ip"`
	FTPPort   int     `json:"ftp_port"`
	CmdPort   int     `json:"cmd_port"`
	PrintCell float64 `json:"print_cell"`
}

var (
	baseDir   string
	cacheDir  string
	cfgPath   string
	vitaMutex sync.Mutex
	safeID    = regexp.MustCompile(`[^A-Za-z0-9_]`)
)

func loadConfig() Config {
	c := Config{IP: "192.168.68.60", FTPPort: 1337, CmdPort: 1338, PrintCell: 1.6}
	if data, err := os.ReadFile(cfgPath); err == nil {
		json.Unmarshal(data, &c)
	}
	return c
}

func saveConfig(c Config) {
	data, _ := json.MarshalIndent(c, "", "  ")
	os.WriteFile(cfgPath, data, 0o644)
}

func writeJSON(w http.ResponseWriter, status int, v any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	json.NewEncoder(w).Encode(v)
}

func fail(w http.ResponseWriter, status int, err error) {
	writeJSON(w, status, map[string]any{"ok": false, "error": err.Error()})
}

func encodePNG(img image.Image) []byte {
	var buf bytes.Buffer
	png.Encode(&buf, img)
	return buf.Bytes()
}

func main() {
	exe, _ := os.Executable()
	baseDir = filepath.Dir(exe)
	if strings.Contains(baseDir, "go-build") { // `go run`
		baseDir, _ = os.Getwd()
	}
	cacheDir = filepath.Join(baseDir, "cache")
	cfgPath = filepath.Join(baseDir, "config.json")
	os.MkdirAll(cacheDir, 0o755)
	port := 8765
	if p, err := strconv.Atoi(os.Getenv("PORT")); err == nil {
		port = p
	}

	http.HandleFunc("/", func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/" {
			http.NotFound(w, r)
			return
		}
		data, _ := web.ReadFile("web/index.html")
		w.Header().Set("Content-Type", "text/html; charset=utf-8")
		w.Write(data)
	})

	http.HandleFunc("/api/config", func(w http.ResponseWriter, r *http.Request) {
		if r.Method == http.MethodPost {
			c := loadConfig()
			var in map[string]any
			json.NewDecoder(r.Body).Decode(&in)
			if ip, ok := in["ip"].(string); ok && ip != "" {
				c.IP = ip
			}
			if cell, ok := in["print_cell"].(float64); ok && cell > 0 {
				c.PrintCell = cell
			}
			saveConfig(c)
		}
		writeJSON(w, 200, loadConfig())
	})

	http.HandleFunc("/api/status", func(w http.ResponseWriter, r *http.Request) {
		c := loadConfig()
		writeJSON(w, 200, map[string]any{"ip": c.IP, "ftp": portOpen(c.IP, c.FTPPort), "cmd": portOpen(c.IP, c.CmdPort)})
	})

	http.HandleFunc("/api/games", func(w http.ResponseWriter, r *http.Request) {
		vitaMutex.Lock()
		defer vitaMutex.Unlock()
		c, err := getFTP(loadConfig())
		if err != nil {
			fail(w, 502, err)
			return
		}
		games := library(c)
		if len(games) == 0 {
			dropFTP()
		}
		state := map[int]map[string]bool{}
		for t, s := range systems {
			state[t] = ftpList(c, printRoot+"/"+s.Folder)
		}
		overrides := readMediaTypes(c)
		for i := range games {
			g := &games[i]
			s := systems[g.Type]
			g.System, g.Kind = s.Folder, s.Kind
			if o, ok := overrides[g.ID]; ok { // "show as" override: the model Float draws for this game
				g.Override = o
				for t, ss := range systems {
					if kindOf[t] == o {
						g.Kind = ss.Kind
					}
				}
			}
			g.Printed = state[g.Type][g.ID+".png"]
			g.DiscArt = state[g.Type][g.ID+"_umd.png"] || state[g.Type][g.ID+"_disc.png"]
			local := filepath.Join(cacheDir, g.ID+".png")
			if _, err := os.Stat(local); err != nil && strings.HasPrefix(g.Icon, "ux0:") {
				if data, err := ftpGet(c, g.Icon); err == nil {
					os.WriteFile(local, data, 0o644)
				}
			}
			if _, err := os.Stat(local); err == nil {
				g.Cover = "/cover/" + g.ID
			}
		}
		if games == nil {
			games = []Game{}
		}
		writeJSON(w, 200, games)
	})

	http.HandleFunc("/cover/", func(w http.ResponseWriter, r *http.Request) {
		id := safeID.ReplaceAllString(strings.TrimPrefix(r.URL.Path, "/cover/"), "")
		http.ServeFile(w, r, filepath.Join(cacheDir, id+".png"))
	})

	http.HandleFunc("/label/", func(w http.ResponseWriter, r *http.Request) {
		id := safeID.ReplaceAllString(strings.TrimPrefix(r.URL.Path, "/label/"), "")
		cached := filepath.Join(cacheDir, "label_"+id+".png")
		if data, err := os.ReadFile(cached); err == nil {
			w.Header().Set("Content-Type", "image/png")
			w.Write(data)
			return
		}
		vitaMutex.Lock()
		defer vitaMutex.Unlock()
		c, err := getFTP(loadConfig())
		if err != nil {
			fail(w, 502, err)
			return
		}
		for _, s := range systems {
			names := []string{}
			if s.Suffix != "" {
				names = append(names, id+s.Suffix)
			}
			names = append(names, id+".png")
			for _, n := range names {
				if data, err := ftpGet(c, printRoot+"/"+s.Folder+"/"+n); err == nil {
					os.WriteFile(cached, data, 0o644)
					w.Header().Set("Content-Type", "image/png")
					w.Write(data)
					return
				}
			}
		}
		http.NotFound(w, r)
	})

	// /api/detect: where the disc is in an uploaded image (for the crop editor)
	http.HandleFunc("/api/detect", func(w http.ResponseWriter, r *http.Request) {
		file, _, err := r.FormFile("image")
		if err != nil {
			fail(w, 400, fmt.Errorf("no image"))
			return
		}
		defer file.Close()
		img, _, err := image.Decode(file)
		if err != nil {
			fail(w, 400, fmt.Errorf("cannot read that image: %v", err))
			return
		}
		src := toRGBA(img)
		c, ok := discFromAlpha(src)
		if !ok {
			c, ok = findDisc(src)
		}
		b := src.Bounds()
		if !ok {
			c = circle{float64(b.Dx()) / 2, float64(b.Dy()) / 2, math.Min(float64(b.Dx()), float64(b.Dy())) * 0.45}
		}
		writeJSON(w, 200, map[string]any{"ok": true, "found": ok, "cx": c.cx, "cy": c.cy, "r": c.r, "width": b.Dx(), "height": b.Dy()})
	})

	http.HandleFunc("/api/label/", func(w http.ResponseWriter, r *http.Request) {
		id := safeID.ReplaceAllString(strings.TrimPrefix(r.URL.Path, "/api/label/"), "")
		t, _ := strconv.Atoi(r.URL.Query().Get("type"))
		s, ok := systems[t]
		if !ok {
			fail(w, 400, fmt.Errorf("unknown media type"))
			return
		}
		kind := r.URL.Query().Get("kind") // effective media (override aware); the folder stays the game's own system
		if kind == "" {
			kind = s.Kind
		}
		name := id + ".png"
		switch kind {
		case "umd":
			name = id + "_umd.png"
		case "disc":
			name = id + "_disc.png"
		}
		path := printRoot + "/" + s.Folder + "/" + name
		cfg := loadConfig()
		var data []byte
		if r.Method == http.MethodPost {
			file, _, err := r.FormFile("image")
			if err != nil {
				fail(w, 400, fmt.Errorf("no image"))
				return
			}
			defer file.Close()
			img, _, err := image.Decode(file)
			if err != nil {
				fail(w, 400, fmt.Errorf("cannot read that image: %v", err))
				return
			}
			var label *image.NRGBA
			cx, _ := strconv.ParseFloat(r.FormValue("cx"), 64)
			cy, _ := strconv.ParseFloat(r.FormValue("cy"), 64)
			cr, _ := strconv.ParseFloat(r.FormValue("r"), 64)
			if kind != "card" && cr > 4 {
				label = discLabel(toRGBA(img), circle{cx, cy, cr}, 512) // circle confirmed in the editor
			} else {
				label, err = makeLabel(img, kind)
				if err != nil {
					fail(w, 400, err)
					return
				}
			}
			if r.FormValue("print") == "1" {
				label = printEffect(label, cfg.PrintCell)
			}
			data = encodePNG(label)
		}
		vitaMutex.Lock()
		defer vitaMutex.Unlock()
		c, err := getFTP(cfg)
		if err != nil {
			fail(w, 502, err)
			return
		}
		os.Remove(filepath.Join(cacheDir, "label_"+id+".png"))
		if r.Method == http.MethodDelete {
			if err := c.Delete(path); err != nil {
				fail(w, 400, err)
				return
			}
			writeJSON(w, 200, map[string]any{"ok": true})
			return
		}
		if err := ftpPut(c, path, data); err != nil {
			dropFTP()
			fail(w, 502, fmt.Errorf("upload failed: %v (if the Vita says it cannot allocate memory, close the game that is running)", err))
			return
		}
		writeJSON(w, 200, map[string]any{"ok": true, "name": name})
	})

	// /api/media_type: POST {"id": "...", "kind": "vita"|"psp"|"psx"|""} ("" removes the override)
	http.HandleFunc("/api/media_type", func(w http.ResponseWriter, r *http.Request) {
		var in struct{ ID, Kind string }
		json.NewDecoder(r.Body).Decode(&in)
		in.ID = safeID.ReplaceAllString(in.ID, "")
		if in.ID == "" || (in.Kind != "" && in.Kind != "vita" && in.Kind != "psp" && in.Kind != "psx") {
			fail(w, 400, fmt.Errorf("bad request"))
			return
		}
		vitaMutex.Lock()
		defer vitaMutex.Unlock()
		c, err := getFTP(loadConfig())
		if err != nil {
			fail(w, 502, err)
			return
		}
		m := readMediaTypes(c)
		if in.Kind == "" {
			delete(m, in.ID)
		} else {
			m[in.ID] = in.Kind
		}
		if err := writeMediaTypes(c, m); err != nil {
			fail(w, 502, err)
			return
		}
		writeJSON(w, 200, map[string]any{"ok": true, "types": m})
	})

	http.HandleFunc("/api/print_all", func(w http.ResponseWriter, r *http.Request) {
		cfg := loadConfig()
		vitaMutex.Lock()
		defer vitaMutex.Unlock()
		c, err := getFTP(cfg)
		if err != nil {
			fail(w, 502, err)
			return
		}
		n := 0
		for _, s := range systems {
			src := "ux0:/data/RetroFlow/COVERS/" + s.Folder
			for name := range ftpList(c, src) {
				lower := strings.ToLower(name)
				if !strings.HasSuffix(lower, ".png") && !strings.HasSuffix(lower, ".jpg") {
					continue
				}
				data, err := ftpGet(c, src+"/"+name)
				if err != nil {
					continue
				}
				img, _, err := image.Decode(bytes.NewReader(data))
				if err != nil {
					continue
				}
				out := printEffect(toRGBA(img), cfg.PrintCell)
				base := strings.TrimSuffix(strings.TrimSuffix(name, filepath.Ext(name)), "")
				if err := ftpPut(c, printRoot+"/"+s.Folder+"/"+base+".png", encodePNG(out)); err == nil {
					n++
				}
			}
		}
		writeJSON(w, 200, map[string]any{"ok": true, "count": n})
	})

	http.HandleFunc("/api/relaunch", func(w http.ResponseWriter, r *http.Request) {
		cfg := loadConfig()
		a := vitaCmd(cfg, "kill "+titleID)
		time.Sleep(2 * time.Second)
		b := vitaCmd(cfg, "launch "+titleID)
		writeJSON(w, 200, map[string]any{"ok": true, "log": []string{a, b}})
	})

	http.HandleFunc("/api/wallpaper", func(w http.ResponseWriter, r *http.Request) {
		file, hdr, err := r.FormFile("image")
		if err != nil {
			fail(w, 400, fmt.Errorf("no image"))
			return
		}
		defer file.Close()
		img, _, err := image.Decode(file)
		if err != nil {
			fail(w, 400, fmt.Errorf("cannot read that image: %v", err))
			return
		}
		out := scaleTo(img, 960, 544)
		name := regexp.MustCompile(`[^A-Za-z0-9 _-]`).ReplaceAllString(strings.TrimSuffix(hdr.Filename, filepath.Ext(hdr.Filename)), "")
		if name == "" {
			name = "Wallpaper"
		}
		vitaMutex.Lock()
		defer vitaMutex.Unlock()
		c, err := getFTP(loadConfig())
		if err != nil {
			fail(w, 502, err)
			return
		}
		if err := ftpPut(c, wallpaperRoot+"/"+name+".png", encodePNG(out)); err != nil {
			fail(w, 502, err)
			return
		}
		writeJSON(w, 200, map[string]any{"ok": true, "name": name + ".png"})
	})

	url := fmt.Sprintf("http://localhost:%d", port)
	fmt.Println("RetroFlow Float Art Manager:", url)
	go func() {
		time.Sleep(600 * time.Millisecond)
		if os.Getenv("NO_BROWSER") != "" {
			return
		}
		switch runtime.GOOS {
		case "windows":
			exec.Command("rundll32", "url.dll,FileProtocolHandler", url).Start()
		case "darwin":
			exec.Command("open", url).Start()
		default:
			exec.Command("xdg-open", url).Start()
		}
	}()
	log.Fatal(http.ListenAndServe(fmt.Sprintf("127.0.0.1:%d", port), nil))
}
