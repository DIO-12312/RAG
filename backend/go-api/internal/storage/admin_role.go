package storage

import (
	"context"
	"database/sql"
	"errors"
)

var (
	ErrInvalidRole = errors.New("invalid user role")
	ErrUserMissing = errors.New("user not found")
	ErrLastAdmin   = errors.New("cannot revoke the last administrator")
)

func UserRoleValid(role string) bool { return role == "user" || role == "admin" }

// UserRole deliberately reads MySQL on every call. JWT and browser state are never role authorities.
func (s *Store) UserRole(ctx context.Context, userID string) (string, error) {
	var role string
	err := s.DB.QueryRowContext(ctx, "SELECT role FROM users WHERE id=?", userID).Scan(&role)
	if errors.Is(err, sql.ErrNoRows) {
		return "", ErrUserMissing
	}
	return role, err
}

// SetUserRole is reserved for the offline admin CLI. Lock every current administrator
// in a stable order so concurrent demotions cannot both remove the last administrator.
func (s *Store) SetUserRole(ctx context.Context, userID, role string) (string, error) {
	if !UserRoleValid(role) {
		return "", ErrInvalidRole
	}
	tx, err := s.DB.BeginTx(ctx, nil)
	if err != nil {
		return "", err
	}
	defer tx.Rollback()

	rows, err := tx.QueryContext(ctx, "SELECT id FROM users WHERE role='admin' ORDER BY id FOR UPDATE")
	if err != nil {
		return "", err
	}
	admins := 0
	for rows.Next() {
		var id string
		if err = rows.Scan(&id); err != nil {
			rows.Close()
			return "", err
		}
		admins++
	}
	err = rows.Err()
	rows.Close()
	if err != nil {
		return "", err
	}

	var previous string
	err = tx.QueryRowContext(ctx, "SELECT role FROM users WHERE id=? FOR UPDATE", userID).Scan(&previous)
	if errors.Is(err, sql.ErrNoRows) {
		return "", ErrUserMissing
	}
	if err != nil {
		return "", err
	}
	if previous == "admin" && role == "user" && admins <= 1 {
		return "", ErrLastAdmin
	}
	if previous != role {
		if _, err = tx.ExecContext(ctx, "UPDATE users SET role=? WHERE id=?", role, userID); err != nil {
			return "", err
		}
	}
	if err = tx.Commit(); err != nil {
		return "", err
	}
	return previous, nil
}
