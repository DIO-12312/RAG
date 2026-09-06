package security

import (
	"crypto/aes"
	"crypto/cipher"
	"crypto/rand"
	"crypto/subtle"
	"encoding/base64"
	"errors"
	"github.com/golang-jwt/jwt/v5"
	"golang.org/x/crypto/argon2"
	"strings"
	"time"
)

func ID() string {
	b := make([]byte, 18)
	if _, err := rand.Read(b); err != nil {
		panic(err)
	}
	return base64.RawURLEncoding.EncodeToString(b)
}
func Hash(password string) string {
	salt := make([]byte, 16)
	_, _ = rand.Read(salt)
	key := argon2.IDKey([]byte(password), salt, 2, 64*1024, 2, 32)
	return base64.RawStdEncoding.EncodeToString(salt) + "." + base64.RawStdEncoding.EncodeToString(key)
}
func Verify(password, hash string) bool {
	parts := strings.Split(hash, ".")
	if len(parts) != 2 {
		return false
	}
	salt, e := base64.RawStdEncoding.DecodeString(parts[0])
	if e != nil || len(salt) != 16 {
		return false
	}
	want, e := base64.RawStdEncoding.DecodeString(parts[1])
	if e != nil || len(want) != 32 {
		return false
	}
	got := argon2.IDKey([]byte(password), salt, 2, 64*1024, 2, 32)
	return subtle.ConstantTimeCompare(got, want) == 1
}

type Vault struct{ aead cipher.AEAD }

func NewVault(key []byte) (*Vault, error) {
	b, e := aes.NewCipher(key)
	if e != nil {
		return nil, e
	}
	a, e := cipher.NewGCM(b)
	return &Vault{a}, e
}
func (v *Vault) Seal(value, owner string) string {
	nonce := make([]byte, v.aead.NonceSize())
	_, _ = rand.Read(nonce)
	return base64.StdEncoding.EncodeToString(v.aead.Seal(nonce, nonce, []byte(value), []byte(owner)))
}
func (v *Vault) Open(value, owner string) (string, error) {
	b, e := base64.StdEncoding.DecodeString(value)
	if e != nil || len(b) < v.aead.NonceSize() {
		return "", errors.New("invalid ciphertext")
	}
	out, e := v.aead.Open(nil, b[:v.aead.NonceSize()], b[v.aead.NonceSize():], []byte(owner))
	return string(out), e
}

type Claims struct {
	CSRF string `json:"csrf"`
	jwt.RegisteredClaims
}

func Sign(key []byte, user string) (string, *Claims, error) {
	c := &Claims{CSRF: ID(), RegisteredClaims: jwt.RegisteredClaims{Subject: user, ID: ID(), Issuer: "rag-product", Audience: jwt.ClaimStrings{"rag-web"}, IssuedAt: jwt.NewNumericDate(time.Now()), ExpiresAt: jwt.NewNumericDate(time.Now().Add(24 * time.Hour))}}
	s, e := jwt.NewWithClaims(jwt.SigningMethodHS256, c).SignedString(key)
	return s, c, e
}
func Parse(key []byte, token string) (*Claims, error) {
	c := &Claims{}
	_, e := jwt.ParseWithClaims(token, c, func(t *jwt.Token) (any, error) { return key, nil }, jwt.WithValidMethods([]string{"HS256"}), jwt.WithIssuer("rag-product"), jwt.WithAudience("rag-web"), jwt.WithExpirationRequired())
	if e == nil && (c.Subject == "" || c.ID == "" || c.CSRF == "") {
		e = errors.New("invalid claims")
	}
	return c, e
}
