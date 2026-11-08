"""Supported unattended installer media and expected installed OS identity."""
from pathlib import Path

UBUNTU = {
    'ubuntu24': ('24.04', 'ubuntu-24.04*-live-server-amd64.iso'),
    'ubuntu22': ('22.04', 'ubuntu-22.04*-live-server-amd64.iso'),
    'ubuntu26': ('26.04', 'ubuntu-26.04*-live-server-amd64.iso'),
}

OTHER_MEDIA = {
    'debian11dvd': 'debian-11.0.0-amd64-DVD-1.iso',
    'debian12netinst': 'debian-12.0.0-amd64-netinst.iso',
    'debian13netinst': 'debian-13.7.0-amd64-netinst.iso',
    'alma98minimal': 'AlmaLinux-9.8-x86_64-minimal.iso',
    'alma102minimal': 'AlmaLinux-10.2-x86_64-minimal.iso',
    'windows2022': 'SERVER_EVAL_x64FRE_en-us.iso',
    'esxi803': 'VMware-ESXi-8.0.3-*-HPE-*.iso',
    'esxi801': 'VMware-ESXi-8.0.1-*-HPE-*.iso',
    'esxi800': 'VMware-ESXi-8.0.0-*-HPE-*.iso',
    'esxi703': 'VMware-ESXi-7.0.3-*-HPE-*.iso',
}


def available(directory=Path('ISO')):
    return [os_id for os_id, (_, pattern) in UBUNTU.items()
            if any(directory.glob(pattern))]


def detected_media(directory=Path('ISO')):
    """Report media present without implying an unattended backend exists."""
    return {os_id: next((path.name for path in sorted(directory.glob(pattern), reverse=True)), None)
            for os_id, pattern in OTHER_MEDIA.items()}


def source_for(os_id, directory=Path('ISO')):
    if os_id not in UBUNTU:
        raise ValueError('Automated installation is not yet implemented for this distribution')
    version, pattern = UBUNTU[os_id]
    matches = sorted(directory.glob(pattern), reverse=True)
    if not matches:
        raise ValueError(f'Place an official Ubuntu Server {version} live ISO in {directory} first')
    return matches[0]


def identity(config):
    os_id = config.get('installation', {}).get('os', 'ubuntu24')
    if os_id not in UBUNTU:
        raise ValueError('Unsupported automated operating system')
    return 'ubuntu', UBUNTU[os_id][0]
