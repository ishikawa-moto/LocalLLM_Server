using System.Net.Http.Headers;
using System.Security.Cryptography.X509Certificates;

namespace LocalBrain.ClientHost;

internal sealed class GatewayClient : IDisposable
{
    private const int MaxRequestBodyBytes = 10 * 1024 * 1024;
    private readonly HttpClient client;
    public GatewayClient(ClientConfig config)
    {
        var handler = new HttpClientHandler();
        handler.ClientCertificates.Add(config.LoadCertificate());
        // This installation uses an offline private CA and publishes no CRL.
        // Normal Windows chain, validity, hostname, and EKU checks still apply.
        handler.CheckCertificateRevocationList = false;
        client = new HttpClient(handler) { BaseAddress = new Uri(config.GatewayBaseUrl), Timeout = Timeout.InfiniteTimeSpan };
    }

    internal GatewayClient(HttpClient client) => this.client = client;

    public async Task<HttpResponseMessage> SendAsync(HttpMethod method, string path, Stream? body, string? contentType,
        string requestId, CancellationToken cancellationToken, IReadOnlyDictionary<string, string>? telemetryHeaders = null)
    {
        using var request = new HttpRequestMessage(method, path);
        request.Headers.TryAddWithoutValidation("X-LocalBrain-Request-Id", requestId);
        if (telemetryHeaders is not null)
            foreach (var (name, value) in telemetryHeaders)
                request.Headers.TryAddWithoutValidation(name, value);
        if (body is not null)
        {
            // HttpListener's InputStream has no computable length. StreamContent
            // then sends chunked data, which the ServerPC Gateway rejects.
            using var buffer = new MemoryStream();
            var chunk = new byte[64 * 1024];
            int count;
            while ((count = await body.ReadAsync(chunk, cancellationToken)) != 0)
            {
                if (buffer.Length + count > MaxRequestBodyBytes)
                    throw new ArgumentException("Request body exceeds the Gateway's 10 MiB limit");
                buffer.Write(chunk, 0, count);
            }
            request.Content = new ByteArrayContent(buffer.ToArray());
            if (MediaTypeHeaderValue.TryParse(contentType, out var mediaType)) request.Content.Headers.ContentType = mediaType;
        }
        return await client.SendAsync(request, HttpCompletionOption.ResponseHeadersRead, cancellationToken);
    }

    public async Task<string> JsonAsync(HttpMethod method, string path, string json, CancellationToken cancellationToken = default)
    {
        await using var body = new MemoryStream(System.Text.Encoding.UTF8.GetBytes(json));
        using var response = await SendAsync(method, path, body, "application/json", Guid.NewGuid().ToString(), cancellationToken);
        var result = await response.Content.ReadAsStringAsync(cancellationToken);
        if (!response.IsSuccessStatusCode) throw new HttpRequestException(result, null, response.StatusCode);
        return result;
    }
    public void Dispose() => client.Dispose();
}
