package gomod

import "testing"

func TestAdd(t *testing.T) {
	cases := []struct {
		name string
		a, b int
		want int
	}{
		{"positive", 2, 3, 5},
		{"negative", -2, -3, -5},
		{"zero", 0, 0, 0},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			if got := Add(tc.a, tc.b); got != tc.want {
				t.Fatalf("Add(%d,%d)=%d want %d", tc.a, tc.b, got, tc.want)
			}
		})
	}
}
