package main

import (
	"crypto/rand"
	"encoding/base64"
	"fmt"
	"os"
	"path/filepath"
)

// Local deployments persist independent encryption and signing keys across restarts.
// Production can supply keys from a secret manager through the environment instead.
func loadKey(envName, name string) (string, error) {
	if value := os.Getenv(envName); value != "" {
		return value, nil
	}
	dir := os.Getenv("PRODUCT_KEY_DIR")
	if dir == "" {
		return "", fmt.Errorf("%s or PRODUCT_KEY_DIR required", envName)
	}
	path := filepath.Join(dir, name)
	mode := os.FileMode(0600)
	if name == "encryption.key" {
		mode = 0640
	}
	if b, e := os.ReadFile(path); e == nil {
		if e = os.Chmod(path, mode); e != nil {
			return "", e
		}
		return string(b), nil
	}
	b := make([]byte, 32)
	if _, e := rand.Read(b); e != nil {
		return "", e
	}
	value := base64.StdEncoding.EncodeToString(b)
	f, e := os.OpenFile(path, os.O_WRONLY|os.O_CREATE|os.O_EXCL, mode)
	if e != nil {
		return "", e
	}
	_, e = f.WriteString(value)
	closeErr := f.Close()
	if e != nil {
		return "", e
	}
	return value, closeErr
}
