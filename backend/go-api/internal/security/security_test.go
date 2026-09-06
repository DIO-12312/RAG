package security

import (
	"bytes"
	"github.com/golang-jwt/jwt/v5"
	"testing"
	"time"
)

func TestPasswordAndVault(t *testing.T) {
	h := Hash("a-long-password")
	if !Verify("a-long-password", h) || Verify("wrong", h) {
		t.Fatal("password check")
	}
	v, e := NewVault(bytes.Repeat([]byte{1}, 32))
	if e != nil {
		t.Fatal(e)
	}
	c := v.Seal("secret", "user/chat")
	if s, e := v.Open(c, "user/chat"); e != nil || s != "secret" {
		t.Fatal(e)
	}
	if _, e := v.Open(c, "other/chat"); e == nil {
		t.Fatal("cross user decryption")
	}
	if _, e := v.Open("invalid", "user/chat"); e == nil {
		t.Fatal("invalid ciphertext accepted")
	}
}
func TestJWT(t *testing.T) {
	key := bytes.Repeat([]byte{2}, 32)
	s, c, e := Sign(key, "user")
	if e != nil {
		t.Fatal(e)
	}
	p, e := Parse(key, s)
	if e != nil || p.Subject != "user" || p.CSRF != c.CSRF {
		t.Fatal(e)
	}
	if _, e := Parse([]byte("wrong"), s); e == nil {
		t.Fatal("wrong key accepted")
	}
	if c.ExpiresAt.Sub(c.IssuedAt.Time).Hours() != 24 {
		t.Fatal("expiry")
	}
	c.ExpiresAt = jwt.NewNumericDate(time.Now().Add(-time.Minute))
	expired, e := jwt.NewWithClaims(jwt.SigningMethodHS256, c).SignedString(key)
	if e != nil {
		t.Fatal(e)
	}
	if _, e = Parse(key, expired); e == nil {
		t.Fatal("expired token accepted")
	}
}
