using System.Net;
using System.Text.Json;
using System.Text.RegularExpressions;

namespace LocalBrain.ClientHost;

internal sealed class Bridge(ClientConfig config, GatewayClient gateway, Audit audit)
{
    private readonly HttpListener listener = new();
    public async Task RunAsync(CancellationToken cancellationToken)
    {
        listener.Prefixes.Add($"http://{System.Net.IPAddress.Loopback}:{config.BridgePort}/"); listener.Start();
        audit.Write("bridge_started", new { address = $"{System.Net.IPAddress.Loopback}:{config.BridgePort}" });
        var heartbeat = HeartbeatAsync(cancellationToken);
        try
        {
            while (!cancellationToken.IsCancellationRequested)
            {
                var context = await listener.GetContextAsync().WaitAsync(cancellationToken);
                _ = Task.Run(() => ForwardAsync(context, cancellationToken), cancellationToken);
            }
        }
        catch (OperationCanceledException) { }
        finally { listener.Stop(); await TryShutdownAsync(); await heartbeat.ConfigureAwait(ConfigureAwaitOptions.SuppressThrowing); }
    }

    private async Task ForwardAsync(HttpListenerContext context, CancellationToken cancellationToken)
    {
        var requestId = context.Request.Headers["X-LocalBrain-Request-Id"];
        if (!Guid.TryParse(requestId, out _)) requestId = Guid.NewGuid().ToString();
        try
        {
            var telemetryHeaders = new Dictionary<string, string>();
            foreach (var name in new[] { "X-LocalBrain-Task-Id", "X-LocalBrain-Request-Role", "X-LocalBrain-Risk-Level",
                                         "X-LocalBrain-Session-Id", "X-LocalBrain-Trace-Id" })
            {
                var value = context.Request.Headers[name];
                if (value is null) continue;
                if (!Regex.IsMatch(value, "^[A-Za-z0-9_.:-]{1,128}$"))
                    throw new ArgumentException($"Invalid telemetry header: {name}");
                telemetryHeaders[name] = value;
            }
            using var response = await gateway.SendAsync(new HttpMethod(context.Request.HttpMethod), context.Request.RawUrl ?? "/",
                context.Request.HasEntityBody ? context.Request.InputStream : null, context.Request.ContentType, requestId!,
                cancellationToken, telemetryHeaders);
            context.Response.StatusCode = (int)response.StatusCode;
            context.Response.ContentType = response.Content.Headers.ContentType?.ToString() ?? "application/octet-stream";
            context.Response.Headers["X-LocalBrain-Request-Id"] = requestId;
            var responseHasBody = context.Request.HttpMethod != "HEAD" &&
                response.StatusCode is not (HttpStatusCode.NoContent or HttpStatusCode.NotModified);
            if (responseHasBody)
            {
                if (response.Content.Headers.ContentLength is long contentLength)
                    context.Response.ContentLength64 = contentLength;
                else
                    context.Response.SendChunked = true;
                await response.Content.CopyToAsync(context.Response.OutputStream, cancellationToken);
            }
            else context.Response.ContentLength64 = 0;
        }
        catch (Exception error) when (error is HttpRequestException or TaskCanceledException)
        {
            audit.Write("bridge_request_failed", new { requestId, error = error.GetType().Name });
            context.Response.StatusCode = 502; context.Response.ContentType = "application/json";
            await JsonSerializer.SerializeAsync(context.Response.OutputStream, new { error = "LocalBrain gateway unavailable", request_id = requestId,
                timestamp = DateTimeOffset.UtcNow, retry = true, log = config.AuditLog }, cancellationToken: cancellationToken);
        }
        catch (ArgumentException error)
        {
            context.Response.StatusCode = 400; context.Response.ContentType = "application/json";
            await JsonSerializer.SerializeAsync(context.Response.OutputStream, new { error = error.Message }, cancellationToken: cancellationToken);
        }
        finally { context.Response.Close(); }
    }

    private async Task HeartbeatAsync(CancellationToken token)
    {
        using var timer = new PeriodicTimer(TimeSpan.FromSeconds(config.HeartbeatSeconds));
        while (await timer.WaitForNextTickAsync(token))
            try { await gateway.JsonAsync(HttpMethod.Post, "/v1/control/heartbeat", "{}", token); }
            catch (Exception error) when (error is HttpRequestException or TaskCanceledException)
            { audit.Write("heartbeat_failed", new { error = error.GetType().Name }); }
    }
    private async Task TryShutdownAsync()
    {
        try { await gateway.JsonAsync(HttpMethod.Post, "/v1/control/client-shutdown", "{}"); } catch { }
    }
}
