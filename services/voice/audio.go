package main

import "encoding/binary"

// G.711 μ-law decode table: byte index → 16-bit PCM sample (scaled to full int16 range).
// Computed per ITU-T G.711 spec: invert bits, extract sign/exponent/mantissa,
// reconstruct magnitude, shift to 16-bit range.
var mulawDecode [256]int16

// Segment lookup for μ-law encoding. Index = (biased_sample >> 7) & 0xFF.
var mulawSegment [256]int8

func init() {
	for i := range 256 {
		v := ^byte(i)
		sign := int16(v>>7) & 1
		exp := int16(v>>4) & 0x07
		man := int16(v) & 0x0F

		sample := ((man << 1) | 0x21) << exp
		sample -= 33
		sample <<= 2 // scale to 16-bit range

		if sign != 0 {
			sample = -sample
		}
		mulawDecode[i] = sample
	}

	for i := range 256 {
		seg := int8(0)
		v := i
		for v > 1 && seg < 7 {
			v >>= 1
			seg++
		}
		mulawSegment[i] = seg
	}
}

func encodeMulaw(sample int16) byte {
	const bias = 0x84
	const clip = 32635

	sign := byte(0)
	if sample < 0 {
		sign = 0x80
		sample = -sample
	}
	if sample > clip {
		sample = clip
	}
	sample += bias

	seg := mulawSegment[(sample>>7)&0xFF]
	man := (sample >> (seg + 3)) & 0x0F
	return ^(sign | byte(seg)<<4 | byte(man))
}

// MulawToLinear16k decodes μ-law (8 kHz) to PCM16-LE (16 kHz) in a single pass.
// Linear interpolation doubles the sample rate. Output is ready for Gemini.
func MulawToLinear16k(mulaw []byte) []byte {
	n := len(mulaw)
	if n == 0 {
		return nil
	}
	out := make([]byte, n*4) // n samples → 2n samples → 4n bytes

	for i := 0; i < n-1; i++ {
		s0 := mulawDecode[mulaw[i]]
		s1 := mulawDecode[mulaw[i+1]]
		mid := int16((int32(s0) + int32(s1)) >> 1)

		binary.LittleEndian.PutUint16(out[i*4:], uint16(s0))
		binary.LittleEndian.PutUint16(out[i*4+2:], uint16(mid))
	}
	last := mulawDecode[mulaw[n-1]]
	binary.LittleEndian.PutUint16(out[(n-1)*4:], uint16(last))
	binary.LittleEndian.PutUint16(out[(n-1)*4+2:], uint16(last))

	return out
}

// abs returns the absolute value of an int.
func abs(x int) int {
	if x < 0 {
		return -x
	}
	return x
}

// downsampleEncode8k converts PCM16-LE at inputRate down to 8 kHz and encodes
// each sample with enc (μ-law or A-law). Instead of naively picking every Nth
// sample — which folds all energy above 4 kHz back into the band as harsh
// aliasing/"robotic" artifacts — it applies a triangular (Bartlett) low-pass
// over each decimation window. For 24→8 kHz that lands the cutoff right around
// the telephone band (~3.4 kHz), so the agent's voice comes through clean.
func downsampleEncode8k(pcm []byte, inputRate int, enc func(int16) byte) []byte {
	ratio := inputRate / 8000
	if ratio < 1 {
		ratio = 1
	}
	nIn := len(pcm) / 2
	nOut := nIn / ratio
	if nOut == 0 {
		return nil
	}
	out := make([]byte, nOut)

	if ratio == 1 {
		for i := range nOut {
			out[i] = enc(int16(binary.LittleEndian.Uint16(pcm[i*2:])))
		}
		return out
	}

	r := ratio // triangular window radius → length 2r+1
	for i := range nOut {
		c := i*ratio + ratio/2
		var sum, wsum int32
		for off := -r; off <= r; off++ {
			j := c + off
			if j < 0 || j >= nIn {
				continue
			}
			w := int32(r + 1 - abs(off))
			sum += w * int32(int16(binary.LittleEndian.Uint16(pcm[j*2:])))
			wsum += w
		}
		out[i] = enc(int16(sum / wsum))
	}
	return out
}

// LinearToMulaw converts PCM16-LE at the given rate to μ-law at 8 kHz (with an
// anti-alias low-pass — see downsampleEncode8k).
func LinearToMulaw(pcm []byte, inputRate int) []byte {
	return downsampleEncode8k(pcm, inputRate, encodeMulaw)
}

// mulawEnergy returns the mean absolute amplitude (int16 scale) of a μ-law frame,
// used to detect when the prospect is actually speaking (vs. line silence) for
// per-turn reply-latency measurement.
func mulawEnergy(mulaw []byte) int {
	if len(mulaw) == 0 {
		return 0
	}
	var sum int64
	for _, b := range mulaw {
		v := int(mulawDecode[b])
		if v < 0 {
			v = -v
		}
		sum += int64(v)
	}
	return int(sum / int64(len(mulaw)))
}

// encodeAlaw converts a 16-bit signed PCM sample to G.711 A-law per ITU-T G.711.
func encodeAlaw(sample int16) byte {
	var sign byte = 0x80
	if sample < 0 {
		sign = 0x00
		sample = -sample - 1
		if sample < 0 {
			sample = 0
		}
	}
	if sample > 32635 {
		sample = 32635
	}

	var compressed byte
	if sample >= 256 {
		seg := byte(1)
		for s := sample >> 8; s > 1 && seg < 7; s >>= 1 {
			seg++
		}
		compressed = (seg << 4) | byte((sample>>(seg+3))&0x0F)
	} else {
		compressed = byte(sample >> 4)
	}

	return (sign | compressed) ^ 0x55
}

// LinearToAlaw converts PCM16-LE at the given rate to A-law at 8 kHz (with the
// same anti-alias low-pass as LinearToMulaw).
func LinearToAlaw(pcm []byte, inputRate int) []byte {
	return downsampleEncode8k(pcm, inputRate, encodeAlaw)
}
