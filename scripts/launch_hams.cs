using System;
using System.Diagnostics;
using System.IO;
using System.Reflection;
using System.Threading;
using System.Windows.Forms;

[assembly: AssemblyTitle("Hams")]
[assembly: AssemblyProduct("Hams")]
[assembly: AssemblyDescription("Hams-Einstellungen oeffnen")]
[assembly: AssemblyVersion("1.0.0.0")]

internal static class HamsLauncher
{
    private const string SettingsEvent = @"Local\Hams.OpenSettings-7a3f2c1b";

    [STAThread]
    private static int Main()
    {
        try
        {
            try
            {
                using (EventWaitHandle request = EventWaitHandle.OpenExisting(SettingsEvent))
                {
                    request.Set();
                    return 0;
                }
            }
            catch (WaitHandleCannotBeOpenedException) { }

            string root = Path.GetDirectoryName(Assembly.GetExecutingAssembly().Location);
            string python = Path.Combine(root, @".venv\Scripts\pythonw.exe");
            string script = Path.Combine(root, @"hams\main.py");
            if (!File.Exists(python) || !File.Exists(script))
                throw new FileNotFoundException("Die Hams-Installation neben dem Startprogramm ist unvollstaendig.");
            Process.Start(new ProcessStartInfo
            {
                FileName = python,
                Arguments = "\"" + script + "\" --settings",
                WorkingDirectory = root,
                UseShellExecute = false,
                CreateNoWindow = true
            });
            return 0;
        }
        catch (Exception error)
        {
            MessageBox.Show(error.Message, "Hams", MessageBoxButtons.OK, MessageBoxIcon.Error);
            return 1;
        }
    }
}
