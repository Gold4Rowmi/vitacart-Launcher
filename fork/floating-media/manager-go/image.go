package main

// Disc detection, label cropping and the printed-sticker look (ports of umd_labels.py and
// print_covers.py).

import (
	"errors"
	"image"
	"image/color"
	"math"
	"sort"

	"golang.org/x/image/draw"
)

type circle struct{ cx, cy, r float64 }

func toRGBA(img image.Image) *image.NRGBA {
	b := img.Bounds()
	out := image.NewNRGBA(image.Rect(0, 0, b.Dx(), b.Dy()))
	draw.Draw(out, out.Bounds(), img, b.Min, draw.Src)
	return out
}

func scaleTo(img image.Image, w, h int) *image.NRGBA {
	out := image.NewNRGBA(image.Rect(0, 0, w, h))
	draw.CatmullRom.Scale(out, out.Bounds(), img, img.Bounds(), draw.Over, nil)
	return out
}

func median(v []float64) float64 {
	s := append([]float64(nil), v...)
	sort.Float64s(s)
	return s[len(s)/2]
}

// findDisc locates the disc in a scan: the largest round, non-background, non-white blob.
func findDisc(src *image.NRGBA) (circle, bool) {
	w, h := src.Rect.Dx(), src.Rect.Dy()
	k := 1.0
	if w > 600 || h > 600 {
		k = math.Max(float64(w), float64(h)) / 600
	}
	sw, sh := int(float64(w)/k), int(float64(h)/k)
	small := scaleTo(src, sw, sh)
	px := func(x, y int) (float64, float64, float64) {
		i := y*small.Stride + x*4
		return float64(small.Pix[i]), float64(small.Pix[i+1]), float64(small.Pix[i+2])
	}
	var br, bg, bb []float64
	for x := 0; x < sw; x++ {
		for _, y := range []int{0, sh - 1} {
			r, g, b := px(x, y)
			br, bg, bb = append(br, r), append(bg, g), append(bb, b)
		}
	}
	for y := 0; y < sh; y++ {
		for _, x := range []int{0, sw - 1} {
			r, g, b := px(x, y)
			br, bg, bb = append(br, r), append(bg, g), append(bb, b)
		}
	}
	mr, mg, mb := median(br), median(bg), median(bb)
	mask := make([]bool, sw*sh)
	var xs, ys []float64
	for y := 0; y < sh; y++ {
		for x := 0; x < sw; x++ {
			r, g, b := px(x, y)
			diff := math.Sqrt((r-mr)*(r-mr) + (g-mg)*(g-mg) + (b-mb)*(b-mb))
			bright := math.Min(r, math.Min(g, b)) > 215
			if diff > 40 && !bright {
				mask[y*sw+x] = true
				xs = append(xs, float64(x))
				ys = append(ys, float64(y))
			}
		}
	}
	if len(xs) < 500 {
		return circle{}, false
	}
	pct := func(v []float64, p float64) float64 {
		s := append([]float64(nil), v...)
		sort.Float64s(s)
		return s[int(float64(len(s)-1)*p)]
	}
	x0, x1 := pct(xs, 0.01), pct(xs, 0.99)
	y0, y1 := pct(ys, 0.01), pct(ys, 0.99)
	cx, cy := (x0+x1)/2, (y0+y1)/2
	r := math.Max(x1-x0, y1-y0) / 2
	best := r
	for i := 0; i < 36; i++ {
		rr := r*0.8 + (r*1.15-r*0.8)*float64(i)/35
		cov := 0.0
		for a := 0; a < 180; a++ {
			ang := 2 * math.Pi * float64(a) / 180
			x := int(math.Max(0, math.Min(float64(sw-1), cx+rr*math.Cos(ang))))
			y := int(math.Max(0, math.Min(float64(sh-1), cy+rr*math.Sin(ang))))
			if mask[y*sw+x] {
				cov++
			}
		}
		if cov/180 > 0.6 {
			best = rr
		}
	}
	return circle{cx * k, cy * k, best * k}, true
}

// discFromAlpha: an image that is already a clean disc on transparency.
func discFromAlpha(src *image.NRGBA) (circle, bool) {
	w, h := src.Rect.Dx(), src.Rect.Dy()
	minx, miny, maxx, maxy := w, h, -1, -1
	clear := 0
	for y := 0; y < h; y++ {
		for x := 0; x < w; x++ {
			a := src.Pix[y*src.Stride+x*4+3]
			if a == 0 {
				clear++
			}
			if a > 32 {
				if x < minx {
					minx = x
				}
				if x > maxx {
					maxx = x
				}
				if y < miny {
					miny = y
				}
				if y > maxy {
					maxy = y
				}
			}
		}
	}
	if maxx < 0 || float64(clear)/float64(w*h) <= 0.1 {
		return circle{}, false
	}
	r := math.Max(float64(maxx-minx), float64(maxy-miny)) / 2
	return circle{float64(minx+maxx) / 2, float64(miny+maxy) / 2, r}, true
}

// discLabel crops the disc to a 512x512 round label on transparency.
func discLabel(src *image.NRGBA, c circle, size int) *image.NRGBA {
	r := c.r * 0.985
	box := image.Rect(int(c.cx-r), int(c.cy-r), int(c.cx+r), int(c.cy+r))
	crop := image.NewNRGBA(image.Rect(0, 0, box.Dx(), box.Dy()))
	draw.Draw(crop, crop.Bounds(), src, box.Min, draw.Src)
	sq := scaleTo(crop, size, size)
	out := image.NewNRGBA(image.Rect(0, 0, size, size))
	R := float64(size)/2 - 1.5
	cc := float64(size) / 2
	for y := 0; y < size; y++ {
		for x := 0; x < size; x++ {
			d := math.Hypot(float64(x)+0.5-cc, float64(y)+0.5-cc)
			a := math.Max(0, math.Min(1, R-d+0.5))
			i := y*out.Stride + x*4
			j := y*sq.Stride + x*4
			out.Pix[i], out.Pix[i+1], out.Pix[i+2] = sq.Pix[j], sq.Pix[j+1], sq.Pix[j+2]
			out.Pix[i+3] = uint8(a * 255)
		}
	}
	return out
}

func makeLabel(img image.Image, kind string) (*image.NRGBA, error) {
	src := toRGBA(img)
	if kind == "card" {
		return src, nil // the cover itself; Float crops it under the blue band
	}
	c, ok := discFromAlpha(src)
	if !ok {
		c, ok = findDisc(src)
	}
	if !ok {
		return nil, errors.New("no disc found in the image")
	}
	return discLabel(src, c, 512), nil
}

// ---------------------------------------------------------------- printed look

func boxBlur(a []float32, w, h, r int) []float32 {
	if r < 1 {
		return a
	}
	tmp := make([]float32, w*h)
	out := make([]float32, w*h)
	for y := 0; y < h; y++ {
		var s float32
		for x := -r; x <= r; x++ {
			s += a[y*w+clampi(x, 0, w-1)]
		}
		for x := 0; x < w; x++ {
			tmp[y*w+x] = s / float32(2*r+1)
			s += a[y*w+clampi(x+r+1, 0, w-1)] - a[y*w+clampi(x-r, 0, w-1)]
		}
	}
	for x := 0; x < w; x++ {
		var s float32
		for y := -r; y <= r; y++ {
			s += tmp[clampi(y, 0, h-1)*w+x]
		}
		for y := 0; y < h; y++ {
			out[y*w+x] = s / float32(2*r+1)
			s += tmp[clampi(y+r+1, 0, h-1)*w+x] - tmp[clampi(y-r, 0, h-1)*w+x]
		}
	}
	return out
}

func clampi(v, lo, hi int) int {
	if v < lo {
		return lo
	}
	if v > hi {
		return hi
	}
	return v
}

func clampf(v, lo, hi float32) float32 {
	if v < lo {
		return lo
	}
	if v > hi {
		return hi
	}
	return v
}

// screen halftones one ink: density 0..1 -> coverage 0..1 (dots on a grid rotated by angle).
func screen(density []float32, w, h int, cell, angle float64, ss int) []float32 {
	c := cell * float64(ss)
	blurred := boxBlur(density, w, h, int(c/2))
	out := make([]float32, w*h)
	ca, sa := math.Cos(angle*math.Pi/180), math.Sin(angle*math.Pi/180)
	edge := float32(0.35 / float64(ss))
	for y := 0; y < h; y++ {
		for x := 0; x < w; x++ {
			u := (float64(x)*ca + float64(y)*sa) / c
			v := (-float64(x)*sa + float64(y)*ca) / c
			du, dv := u-math.Floor(u)-0.5, v-math.Floor(v)-0.5
			dist := float32(math.Sqrt(du*du + dv*dv))
			cu, cv := math.Floor(u)+0.5, math.Floor(v)+0.5
			cx := (cu*ca - cv*sa) * c
			cy := (cu*sa + cv*ca) * c
			xi := clampi(int(math.Round(cx)), 0, w-1)
			yi := clampi(int(math.Round(cy)), 0, h-1)
			d := clampf(blurred[yi*w+xi], 0, 1)
			r := float32(math.Sqrt(float64(d) / math.Pi))
			out[y*w+x] = clampf((r-dist)/edge+0.5, 0, 1)
		}
	}
	return out
}

// printEffect gives cover art the look of a CMYK-printed sticker. Alpha is kept.
func printEffect(src *image.NRGBA, cell float64) *image.NRGBA {
	const ss = 3
	const gcr, mix, grain = 0.85, 0.35, 0.012
	paper := [3]float32{0.985, 0.978, 0.962}
	w0, h0 := src.Rect.Dx(), src.Rect.Dy()
	w, h := w0*ss, h0*ss
	big := scaleTo(src, w, h)
	n := w * h
	cI, mI, yI, kI := make([]float32, n), make([]float32, n), make([]float32, n), make([]float32, n)
	for i := 0; i < n; i++ {
		r := float32(big.Pix[i*4]) / 255
		g := float32(big.Pix[i*4+1]) / 255
		b := float32(big.Pix[i*4+2]) / 255
		k := (1 - float32(math.Max(float64(r), math.Max(float64(g), float64(b))))) * gcr
		den := 1 - k + 1e-6
		cI[i] = clampf((1-r-k)/den, 0, 1)
		mI[i] = clampf((1-g-k)/den, 0, 1)
		yI[i] = clampf((1-b-k)/den, 0, 1)
		kI[i] = k
	}
	inks := [][]float32{screen(cI, w, h, cell, 15, ss), screen(mI, w, h, cell, 75, ss), screen(yI, w, h, cell, 0, ss), screen(kI, w, h, cell, 45, ss)}
	cols := [][3]float32{{0.0, 0.62, 0.90}, {0.90, 0.07, 0.50}, {1.0, 0.90, 0.05}, {0.10, 0.10, 0.12}}
	out := image.NewNRGBA(image.Rect(0, 0, w, h))
	for i := 0; i < n; i++ {
		px := paper
		for j, ink := range inks {
			a := ink[i]
			for ch := 0; ch < 3; ch++ {
				px[ch] = px[ch]*(1-a) + px[ch]*cols[j][ch]*a
			}
		}
		for ch := 0; ch < 3; ch++ {
			out.Pix[i*4+ch] = uint8(clampf(px[ch], 0, 1) * 255)
		}
		out.Pix[i*4+3] = 255
	}
	// soften a touch (3x3 box at the supersampled size), then back down, mix in some original, grain
	for ch := 0; ch < 3; ch++ {
		plane := make([]float32, n)
		for i := 0; i < n; i++ {
			plane[i] = float32(out.Pix[i*4+ch])
		}
		plane = boxBlur(plane, w, h, 1)
		for i := 0; i < n; i++ {
			out.Pix[i*4+ch] = uint8(plane[i])
		}
	}
	res := scaleTo(out, w0, h0)
	seed := uint32(7)
	for i := 0; i < w0*h0; i++ {
		seed = seed*1664525 + 1013904223
		noise := (float32(seed>>8)/float32(1<<24) - 0.5) * 2 * grain * 255 * 1.7
		for ch := 0; ch < 3; ch++ {
			v := float32(res.Pix[i*4+ch])*(1-mix) + float32(src.Pix[i*4+ch])*mix + noise
			res.Pix[i*4+ch] = uint8(clampf(v, 0, 255))
		}
		res.Pix[i*4+3] = src.Pix[i*4+3]
	}
	return res
}

var _ = color.RGBA{}
