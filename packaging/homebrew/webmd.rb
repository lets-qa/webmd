class Webmd < Formula
  include Language::Python::Virtualenv

  desc "Serve a directory in the browser, rendering Markdown files as web pages"
  homepage "https://github.com/lets-qa/webmd"
  url "https://files.pythonhosted.org/packages/aa/45/44506cb2220abc4405e7ce5be46cf15365b915e8a52998084961f7dace0c/webmd-0.2.0.tar.gz"
  sha256 "9fbf8be39972e7708f02041eababc8fe405737712db876383420f8b5bb14c75f"
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
