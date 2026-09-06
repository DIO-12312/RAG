package agent

import (
	"context"
	"fmt"
	"net"
	"net/http"
	"time"
)

// Resolve and validate each connection, then dial that exact IP to prevent DNS rebinding.
func ModelClient(allowLocal bool) *http.Client {
	return &http.Client{CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }, Transport: &http.Transport{TLSHandshakeTimeout: 10 * time.Second, ResponseHeaderTimeout: 120 * time.Second, DialContext: func(ctx context.Context, network, address string) (net.Conn, error) {
		host, port, e := net.SplitHostPort(address)
		if e != nil {
			return nil, e
		}
		ips, e := net.DefaultResolver.LookupIPAddr(ctx, host)
		if e != nil {
			return nil, e
		}
		for _, item := range ips {
			ip := item.IP
			if !allowLocal && (!ip.IsGlobalUnicast() || ip.IsPrivate() || ip.IsLoopback() || ip.IsLinkLocalUnicast()) {
				return nil, fmt.Errorf("model endpoint resolves to restricted address")
			}
		}
		var last error
		for _, item := range ips {
			c, e := (&net.Dialer{Timeout: 10 * time.Second}).DialContext(ctx, network, net.JoinHostPort(item.IP.String(), port))
			if e == nil {
				return c, nil
			}
			last = e
		}
		if last == nil {
			last = fmt.Errorf("model endpoint has no addresses")
		}
		return nil, last
	}}}
}
