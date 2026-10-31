package main

import (
	"image"
	"image/color"
	"os"
	"testing"
)

func TestCardSticker(t *testing.T) {
	src := image.NewNRGBA(image.Rect(0, 0, 250, 320))
	for y := 0; y < 320; y++ {
		for x := 0; x < 250; x++ {
			src.Set(x, y, color.NRGBA{uint8(x), uint8(y / 2), 120, 255})
		}
	}
	out := cardSticker(src, "PCSE00912")
	if out.Bounds().Dx() != 512 || out.Bounds().Dy() != 556 {
		t.Fatalf("size %v", out.Bounds())
	}
	if p := os.Getenv("STICKER_OUT"); p != "" {
		os.WriteFile(p, encodePNG(out), 0o644)
	}
}
