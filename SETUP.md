## Setup overview

The following is an overview of how this system could be deployed on a FreeBSD system.

Assumptions:

* Jails are on a ZFS zpool under `zroot/jail` 
* Each jail has an integer ID number (referred to here as `JAIL_ID`)
* Jails are connected to the host's internal network `192.168.1.0/24` via 
  `epair` interfaces on `bridge0` with address `192.168.1.1`
* Jails also have an "anycast bridge" `bridge1` with address `10.0.254.1` on
  `10.0.254.0/24`
* Jails are connected to both bridges separately with `epair` interfaces
* VPN jails having `JAIL_ID` values which start at a certain offset. In this case
we will assume the offset is `50`, so that we can say that `dynvpn0` has a 
`JAIL_ID` of `0`, `dynvpn15` has a `JAIL_ID` of `65`, etc.
* The host firewall is configured consistently with `README.md`, allowing
connectivity between the different components, etc. 

### `vpndns`

1. Create a jail called `vpndns` 
1. Install Perl dependencies in the jail (see `README.md`)
1. Either plan to run the daemon as `root` (discouraged), or configure NAT in 
the jail to forward a non-privileged port to `53` locally, or modify 
 `net.inet.ip.portrange.reservedhigh`
1. Configure starting from the sample configuration file 


### `jail` (VPN container setup)

#### Basic setup

1. Create a jail filesystem / install a base system onto `zroot/jail/dynvpn/base`.
This will be the "jail base" that `dynvpn` instances are cloned from.
1. In the base jail, create a user `openvpn` with home directory `/usr/home/openvpn`,
and create the directory `/mnt/vpn`. Also set up public-key authentication for the 
`openvpn` user.
1. Copy the contents of `jail/` from this repository into location on the host
which we will refer to as `$BASE`
1. Ensure permissions are set as follows, or similar, for the directories inside
`$BASE`: read-only for users other
than root, except for the `state`, `log`, and `pid` directories which should be
owned by the `openvpn` user created above (based on UID)
1. Ensure the base jail has snapshots so that it can be cloned
1. Copy `vars.sh.sample` to `vars.sh` and update it as necessary to fit the 
host environment. `VPN_ID_OFFSET` is the ID offset mentioned under Assumptions,
above.
1. Copy the contents of `jail/files/host/devfs.rules` in this repository 
into the host's 
`/etc/devfs.rules`. If one does not exist, start with the template in
`/etc/defaults/devfs.rules`. 

#### Configuration of the VPN container

This section assumes that OpenVPN configuration will be generated from files 
present in `$BASE/etc/openvpn`, using a method similar to what is available
in `$BASE/scripts/generate-config.sh.sample`.

1. Navigate to `$BASE/etc/openvpn`
   * In that directory, add OpenVPN client configuration files with paths of the
form `PROVIDER/FILE`. These two identifiers will be used to specify which 
configuration to use for a given VPN container.
1. Configure a `dynvpn` jail, for example, `dynvpn0`:
   1. Create a file `$BASE/etc/vpn/dynvpn0` with the following format:
        ```
        PROVIDER FILE
        ```

      where `PROVIDER` and `FILE` correspond to the identifiers from the previous
step. In this way, the specific provider and configuration file (e.g., representing
the provider's specific OpenVPN server to connect to) can be specified.
   
    1. Edit `$BASE/etc/rc-conf/dynvpn0.conf`, which will be copied into 
`/etc/rc.conf.local` in the VPN container

1. Start the jail using `start-jail.sh`:
    ```
    # ./start-jail.sh JAIL_ID
    ```

   where `JAIL_ID` is the numeric VPN jail ID (not the name), such as `0` 
(not `dynvpn0`). This will clone it from the dynvpn base,
copy certain files over, and start the jail. The VPN connection will not be
started: it is started as appropriate by the `dynvpn.py` program.

Once these steps are completed, it should be possible to start an OpenVPN
session using the `vpn-set-online.sh` script from the `dynvpn/` part of the
repository, with appropriate values, such as:
```
dynvpn@ipsec.host1 $ ./vpn-set-online.sh dynvpn0 192.168.1.50 /mnt/vpn
```

### `dynvpn`

1. Create or use an existing "IPsec" jail, as described in `README.md`. 
We will run the `dynvpn` program
to manage active VPN connections in this jail. The remaining steps take
place inside this jail.
   * The reason for running the program in this jail is that an
active VPN connection will have the VPN container's anycast address advertised
over BGP as described in `README.md`, since the easiest way to do this is to 
add the route to the routing table local to the BGP daemon (which in our 
setup, runs in the IPsec jail). 
But the `dynvpn` program and scripts can
be modified to accommodate a different setup, so an "IPsec jail" may not be 
necessary.  
   * So, we will assume the use of an "IPsec jail" configured and used consistently
with what is described in `README.md`, but that does not preclude other
configurations.

1. Create a `dynvpn` user and copy the contents of the `dynvpn/` directory in
this repository into the user's home directory.
1. Setup and install `net/frrouting` and install a `bgpd.conf` in 
`/usr/local/etc/frr/bgpd.conf` similar to the sample available in this
repository under `dynvpn/files/bgpd.conf`
1. Install `security/sudo` and copy `dynvpn/files/dynvpn-sudo` from this repository
to `/usr/local/etc/sudoers.d/`. 
1. Install and configure `security/strongswan`, for example using a `swanctl.conf` 
similar to `dynvpn/files/swanctl.conf` in this repository.
1. Place the keys for the `openvpn` user, created earlier (in the `jail` step), in 
`~dynvpn/.ssh/` as `id.openvpn` and `id.openvpn.pub`.
1. Configure `local.yml` and `global.yml` based on the samples provided
1. Install `dynvpn` as a Python package with dependencies using `pip` and the
provided `pyproject.toml`; alternatively, install the dependencies manually
and add the `src` directory to your `PYTHONPATH`.


Once these steps are completed, it should be possible to run the `dynvpn.py`
program on multiple IPsec-connected hosts to maintain VPN connectivity in
a fault-tolerant manner:
```
dynvpn@ipsec.host1 $ python3.11 -m dynvpn --site-id MYSITE --local-config local.yml --global-config global.yml
```


