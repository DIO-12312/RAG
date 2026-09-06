package httpapi

import (
	"bytes"
	"net/http"
	"net/http/httptest"
	"testing"
)

func TestUnauthenticatedAndCrossOrigin(t *testing.T) {
	s := &Server{JWTKey: bytes.Repeat([]byte{1}, 32), Origin: "https://product.test"}
	r := s.Router()
	for _, path := range []string{"/me", "/datasets", "/settings", "/conversations"} {
		w := httptest.NewRecorder()
		r.ServeHTTP(w, httptest.NewRequest("GET", path, nil))
		if w.Code != 401 {
			t.Fatalf("%s: %d", path, w.Code)
		}
	}
	req := httptest.NewRequest("POST", "/auth/login", nil)
	req.Header.Set("Origin", "https://evil.test")
	w := httptest.NewRecorder()
	r.ServeHTTP(w, req)
	if w.Code != http.StatusForbidden {
		t.Fatal(w.Code)
	}
}
func TestModelURLRestrictions(t *testing.T) {
	for _, u := range []string{"file:///etc/passwd", "http://example.com/v1", "https://user:pass@example.com", "https://127.0.0.1", "https://169.254.169.254"} {
		if validURL(u, false) {
			t.Fatal("accepted", u)
		}
	}
	if !validURL("https://models.example.com/v1", false) {
		t.Fatal("valid URL rejected")
	}
}
