using LocalBrain.ClientHost;

try
{
    var config = ClientConfig.Load();
    var audit = new Audit(config);
    var command = args.FirstOrDefault()?.ToLowerInvariant() ?? "serve";
    if (command == "register-project")
    {
        Console.WriteLine(new ProjectAllowlist(config).Register(args.ElementAtOrDefault(1) ?? Environment.CurrentDirectory));
    }
    else if (command == "mcp")
    {
        using var gateway = new GatewayClient(config);
        await new McpServer(gateway).RunAsync();
    }
    else if (command == "review")
    {
        await new ReviewRunner(config, new ProjectAllowlist(config), audit).RunAsync(args.Skip(1).ToArray());
    }
    else if (command == "serve")
    {
        using var gateway = new GatewayClient(config); using var stop = new CancellationTokenSource();
        Console.CancelKeyPress += (_, eventArgs) => { eventArgs.Cancel = true; stop.Cancel(); };
        await new Bridge(config, gateway, audit).RunAsync(stop.Token);
    }
    else throw new ArgumentException("Commands: serve, mcp, review, register-project");
}
catch (Exception error)
{
    Console.Error.WriteLine(System.Text.Json.JsonSerializer.Serialize(new { error = error.Message, cause = error.GetType().Name,
        timestamp = DateTimeOffset.UtcNow }));
    Environment.ExitCode = 1;
}
