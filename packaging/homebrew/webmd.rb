class Webmd < Formula
  include Language::Python::Virtualenv

  desc "Serve a directory in the browser, rendering Markdown files as web pages"
  homepage "https://github.com/lets-qa/webmd"
  url "https://files.pythonhosted.org/packages/0d/86/0da41f8354bd360217990d50540ef48f18dfc62cefddb8b96d8a2c6d8655/webmd-0.2.1.tar.gz"
  sha256 "4461a47810d7c500f3903781945b884ae7339f7f64b7c7f4833759fd36454051"
  license all_of: [
    "MIT",
    "BSD-3-Clause",
    any_of: ["MPL-2.0", "Apache-2.0"],
  ]

  depends_on "cryptography"
  depends_on "python@3.14"

  def install
    virtualenv_install_with_resources
  end

  test do
    assert_match version.to_s, shell_output("#{bin}/webmd --version")

    (testpath/"site").mkpath
    (testpath/"site/README.md").write "# Hello Homebrew\n"
    port = free_port
    pid = spawn bin/"webmd", "--port", port.to_s, testpath/"site"
    begin
      sleep 3
      output = shell_output("curl -s http://127.0.0.1:#{port}/README.md")
      assert_match "Hello Homebrew", output
      # 0.2.1+: assets are bundled, not loaded from a CDN
      assert_match "/__webmd__/static/vendor/purify.min.js", output
      assert_match "DOMPurify", shell_output("curl -s http://127.0.0.1:#{port}/__webmd__/static/vendor/purify.min.js")
    ensure
      Process.kill("TERM", pid)
      Process.wait(pid)
    end
  end
end
