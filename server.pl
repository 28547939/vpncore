#!/usr/local/bin/perl 

use strict;
use warnings;


#BEGIN {
#sub POE::Kernel::TRACE_EVENTS { 1 }
#sub POE::Kernel::TRACE_SIGNALS { 1 }
#sub POE::Kernel::CATCH_EXCEPTIONS { 0 }
#sub POE::Kernel::ASSERT_DEFAULT { 0 }
#sub POE::Kernel::ASSERT_EVENTS { 0 }
#}

use POE qw/Kernel/;


my $config_path = $ARGV[0];

vpndns::server->start($config_path);

POE::Kernel->run();

package vpndns::config;

use YAML::XS qw//;

sub load {
	my $config_path = shift;
	my $config = YAML::XS::LoadFile($config_path);
	return $config;
}

package vpndns::log;

use POE qw/Kernel/;


sub start {
	my ($package, %config) = @_;

	POE::Session->create(
		inline_states	=> {
			_start	=> sub {
				my ($k, $h) = @_[KERNEL, HEAP];

				$k->alias_set('log');

				if (defined $config{output_pipe}) {
#					push $h->{output_fh}, 
				} 
				
				if (defined $config{output_stdout} && $config{output_stdout} == 1) {
					push @{ $h->{output_fh} }, \*STDOUT;
					push @{ $h->{error_fh} }, \*STDOUT;
				}
			},

			output	=> sub {
				my ($k, $h, $caller, $data) = @_[KERNEL, HEAP, SENDER, ARG0];
				my $name = ($k->alias_list($caller))[0];

				for my $fh (@{ $h->{output_fh} }) {
					print $fh "[$name] $data\n";
					$fh->flush;
				}
			},

			error	=> sub {
				my ($k, $h, $caller, $data) = @_[KERNEL, HEAP, SENDER, ARG0];
				my $name = ($k->alias_list($caller))[0];

				for my $fh (@{ $h->{error_fh} }) {
					print $fh "[$name] $data\n";
					$fh->flush;
				}
				
			},
		},
	);

}


# error log
sub EL ($) {
	POE::Kernel->call('log' => 'error' => $_[0]);
}

# log info, debug, etc, to stdout or to a given fifo pipe
sub L ($) {
	POE::Kernel->call('log' => 'output' => $_[0]);
}

BEGIN {
	use Exporter 'import';
	our @ISA = qw/Exporter/;
	our @EXPORT = qw/EL L/;
}


#####################################################################################
# 

package vpndns::blocklist;

use strict;
use warnings;

use File::Find;
use Data::Dumper;

use POE qw/Kernel/;

BEGIN { vpndns::log->import; }

# It was found that the most efficient way to store, parse, and query the blocklist
# is to just read it into a Perl list, sort it, and query it with binary search

# TODO return errors instead of using EL, to allow calling from both the Interface and the main session

# private variables
my $blocklist = [];
my $exceptions = {};
my $nxdomain_regex = [];

sub start {
	my %config = @_;

	POE::Session->create(
		inline_states	=> {
			_start	=> sub {
				my ($k, $h) = @_[KERNEL, HEAP];

				$k->alias_set('blocklist');

				for my $re (@{ $config{nxdomain_regex} }) {
					eval {
						push @$nxdomain_regex, qr/$re/;
					};
					if ($@) {
						EL "could not process element $re on nxdomain_regex: $@$!";
					}
				}

				$k->yield('load', @config{qw/blocklist_dir print_blocklist_read_progress/});
			},
		},

		package_states	=> [
			'vpndns::blocklist'	=> {
				'lookup'		=> 'lookup_main',
				'load'			=> 'read_list',
				'add_exceptions'	=> 'add_exceptions',
				'del_exceptions'	=> 'del_exceptions',
				'list_exceptions'	=> 'list_exceptions',
				'exists_exception'	=> 'exists_exception',
			}
		]
	);
}



# returns any error as a string, to aid any future work to reload the blocklist from the HTTP interface
sub read_list {
	my ($k, $h, $blocklist_dir, $print_progress) = @_[KERNEL, HEAP, ARG0, ARG1];
	my $stdout = \*STDOUT;

	my @rpz;
	my $c = 0;
	L 'reading blocklist';
	eval {
		File::Find::find({
			no_chdir	=> 1,
			wanted 		=> sub {

				open(my $fh, '<', $File::Find::name) or do {
					my $e = "could not read file $File::Find::name: $!";
					EL $e;
					return $e;
				};

				while (<$fh>) {
					if ($print_progress == 1 && $c++ % 1000 == 0) {
						print ".";
						$stdout->flush;
					}

					chomp($_);
					push @rpz, $_;
				}

			}
		}, $blocklist_dir);
	}; 
	if ($@ or $!) {
		my $e = "could not read blocklists from '$blocklist_dir': $@$!";
		EL $e;
		return $e;
	}

	print "\n";

	L 'read new blocklist contents';

	my @x = sort @rpz;
	L 'sorted new blocklist';

	$blocklist = \@x;
	L 'installed new blocklist';
}

sub add_exceptions {
	my ($k, $h, $list) = @_[KERNEL, HEAP, ARG0];

	foreach my $x (@$list) {
		$exceptions->{$x} = 1;
	}
}

sub exists_exception {
	my ($k, $h, $arg) = @_[KERNEL, HEAP, ARG0];

	return exists $exceptions->{$arg};
}

sub del_exceptions {
	my ($k, $h, $list) = @_[KERNEL, HEAP, ARG0];

	foreach my $x (@$list) {
		delete $exceptions->{$x};
	}
}

sub list_exceptions {
	my ($k, $h) = @_[KERNEL, HEAP];

	return keys %$exceptions;
}

sub lookup_main {
	my ($k, $h, $arg) = @_[KERNEL, HEAP, ARG0];

	if (lookup_list($arg) == 1) {
		return ( 1, '127.0.0.1' );
	} elsif (lookup_regex($arg) == 1) {
		return ( 1, 'NXDOMAIN' );
	}

	return (0, undef);
}


sub lookup_regex {
	my $arg = shift;

	for my $re (@$nxdomain_regex) {
		if ($arg =~ $re) {
			return 1
		}
	}

	return 0;
}

sub lookup_list {
	my $arg = shift;

	if (exists $exceptions->{$arg}) {
		L sprintf('%s matched exception, skipping lookup', $arg);
		return 0;
	}

	my $lower = 0;
	my $upper = scalar @$blocklist - 1;

	my $start = [ Time::HiRes::gettimeofday() ];

	while (1) {
		my $i = int(($lower + $upper) / 2);

		#print "lower $lower i $i upper $upper\n";

		my $r = $arg cmp $blocklist->[$i];

		#print "r $r entry $blocklist->[$i]\n";

		if ($r == 0) {
			#print "match $arg $blocklist->[$i]\n";
			return 1;
			#last;
		} elsif ($upper - $lower == 1) {
			if ($blocklist->[$upper] eq $arg or $blocklist->[$lower] eq $arg) {
				#print "match $arg $blocklist->[$i]\n";
				return 1;
			} else {
				#print "no match $blocklist->[$upper] $blocklist->[$lower] \n";
				return 0;
			}

			last;
		} else {
			if ($r == -1) {
				$upper = $i;
				#print "changing upper to $upper\n";
			} elsif ($r == 1) {
				$lower = $i;
			} else {
			}
		}
	}


	L Time::HiRes::tv_interval($start);
	return 0;
}

#####################################################################################
# Locally stored static DNS A records
# Code is structured similarly to the blocklist - read and store records from a file,
# 	perform lookups, and reload when requested


package vpndns::static_records;

use strict;
use warnings;

use Net::DNS::RR qw//;
use Net::DNS::RR::PTR qw//;
use POE qw/Kernel/;
use Data::Dumper;

BEGIN { vpndns::log->import; }

# private variables
my $records = {};
my $ptr_records = {};
my $static_records_filename;
my $static_records_auto_ptr;

sub start {
	my %config = @_;

	POE::Session->create(
		inline_states	=> {
			_start	=> sub {
				my ($k, $h) = @_[KERNEL, HEAP];

				$k->alias_set('static_records');
				$static_records_filename = $config{static_records};
                $static_records_auto_ptr = $config{static_records_auto_ptr};

				$k->yield('load');
			},
		},

		package_states	=> [
			'vpndns::static_records'	=> {
				'lookup_a'		=> 'lookup_a',
				'lookup_ptr'	=> 'lookup_ptr',
				'load'			=> 'load',
			}
		]
	);
}

sub load {
	my ($k, $h) = @_[KERNEL, HEAP];
	my $stdout = \*STDOUT;

	my %records_tmp;
	my %ptr_records_tmp;
	my $c = 0;
	L 'reading static records';
	
	my $filename = $static_records_filename;

	my @error;
	open(my $fh, '<', $filename) or do {
		my $error = "could not read file '$filename': $!";
		EL $error;
		push @error, $error;

		return {
			error	=> \@error
		};
	};

	my $successful = 0;
	my $total = 0;

	while (<$fh>) {
		chomp($_);
		m/^\s*(\S+)\s+(\S+)\s*$/ or do {
			my $error = "could not parse two-field record from line $_";
			push @error, $error;
			EL $error;
			next;
		};

		my ($name, $addr) = ($1, $2);
		eval {
			my $a_rr = Net::DNS::RR->new(
				owner   => $name,
				ttl     => 86400,
				class   => 'IN',
				type    => 'A',
				address => $addr,
			);
			
			my $ptr_rr = Net::DNS::RR::PTR->new(
				owner   => $addr,
				ttl     => 86400,
				class   => 'IN',
				type    => 'PTR',
			);

            $ptr_rr->ptrdname($name);

			$records_tmp{$name} = $a_rr;

            if ($static_records_auto_ptr == 1) {
                $ptr_records_tmp{$addr} = $ptr_rr;
            }
		};

		if ($@) {
			my $error = "could not create DNS record from line $_ ($name -> $addr): $@";
			EL $error;
			push @error, $error;
		} else {
			$successful++;
		}

		$total++;
	}

	$records = \%records_tmp;
	$ptr_records = \%ptr_records_tmp;
	L sprintf("installed %d new static_records with %d errors", $successful, scalar @error);

	return {
		error		=> \@error,
		successful	=> $successful,
		total		=> $total,
	};
}

sub lookup_a {
	my ($k, $h, $arg) = @_[KERNEL, HEAP, ARG0];

	return $records->{$arg};
}

sub lookup_ptr {
	my ($k, $h, $arg) = @_[KERNEL, HEAP, ARG0];

    # for now, just parse the string
    if ($arg =~ m/^(\d+)\.(\d+)\.(\d+)\.(\d+)\.in-addr.arpa/) {
        $arg = "$4.$3.$2.$1";
        return $ptr_records->{$arg};
    } else {
        EL sprintf('could not parse IP address for PTR request %s', $arg);
        return undef;
    }
}

#####################################################################################
#

package vpndns::interface;


use JSON::XS;

BEGIN { vpndns::log->import; }

use POE qw/Component::Server::HTTP/;
use Data::Dumper;

sub json_response {
	my ($response_obj, $http_code, $is_error, $message, $data) = @_;

	my $struct = {
		message 	=> $message,
		is_error	=> $is_error,
		data		=> $data,
	};

	my $json = JSON::XS->new->utf8->pretty;

	#print Dumper($response_obj);

	$response_obj->code($http_code);
	$response_obj->content_type('application/json');
	$response_obj->content($json->encode($struct));
	return $response_obj;
}

sub build_404 {
	my ($response_obj) = @_;

	return &json_response($response_obj, 404, 1, 'not found', {});
}

sub build_402 {
	my ($response_obj, $message) = @_;

	return &json_response($response_obj, 402, 1, $message, {});
}


sub start {
	my %config = @_;

	POE::Session->create(
		inline_states	=> {
			_start	=> sub {
				my ($k, $h, $s) = @_[KERNEL, HEAP, SESSION];

				$k->alias_set('vpndns-interface');

				if (defined $config{ interface_listen }) {
					for my $entry (@{ $config{interface_listen} }) {
						# TODO error handling for malformed values or aborted server start
						&httpd_start($entry->[0], $entry->[1], sub {
							return $s->callback($_[0]);
						});
					}
				}
			},

			# /blocklist/*
			# See config sample for documentation
			blocklist	=> sub {
				my ($k, $h, $args) = @_[KERNEL, HEAP, ARG1];
				my ($req, $resp) = @$args;

				my $path = $req->header('X-Actual-Path');

				my $method = $req->method;

				if ($method eq 'POST') {
					if ($path =~ '/blocklist/add_exception/([^\/]+)$') {
						my $arg = $1;

						if ($k->call('blocklist', 'exists_exception', $arg)) {
							&json_response($resp, 200, 1, 'already exists', {
								error	=> sprintf('the exception %s is already in place', $arg),
							});
						} else {
							$k->call('blocklist', 'add_exceptions', [ $arg ]);
							&json_response($resp, 200, 0, 'success', {
								added	=> $arg,
							});
						}
					} elsif ($path =~ '/blocklist/del_exception/([^\/]+)$') {
						my $arg = $1;
						if ($k->call('blocklist', 'exists_exception', $arg)) {
							my $r = $k->call('blocklist', 'del_exceptions', [ $arg ]);
							&json_response($resp, 200, 0, 'success', {
								deleted	=>	$arg
							});
						} else {
							&json_response($resp, 200, 1, "the specified exception does not exist", [
								$arg
							]);
						}
					} else {
						&build_404($resp, '');
					}
				} elsif ($method eq 'GET') {

					if ($path =~ '/blocklist/list_exceptions(?:\/?)$') {
						my @r = $k->call('blocklist', 'list_exceptions');

						&json_response($resp, 200, 0, 'success', {
							exceptions	=> \@r
						});
					} else {
						&build_404($resp, '');
					}
				} else {
					&build_402($resp, '');
				}

				return RC_OK;
			},

			# /static_records/*
			# See config sample for documentation
			static_records	=> sub {
				my ($k, $h, $caller, $args) = @_[KERNEL, HEAP, SENDER, ARG1];
				my ($req, $resp) = @$args;

				my $path = $req->header('X-Actual-Path');

				my $method = $req->method;
				if ($method eq 'POST') {
					if ($path eq '/static_records/reload') {
						my $r = $k->call('static_records', 'load');
						&json_response($resp, 200, 
							(scalar @{ $r->{ error } } > 0) ? 1 : 0, 
							'completed', $r
						);
					} else {
						&build_404($resp, 'static_records: usage: POST /static_records/reload');
					}

				} else {
					&build_402($resp, 'static_records: usage: POST /static_records/reload');
				}

				return RC_OK;
			},

			notfound => sub {
				my ($k, $h, $c, $args) = @_[KERNEL, HEAP, SENDER, ARG1];
				my ($req, $resp) = @$args;
				
				&build_404($resp, '');
				return RC_OK;
			},
		},
	);
}


sub httpd_start {
	my ($listen_addr, $listen_port, $callback) = @_;

	my @handlers = (
		[ '^/blocklist/.+'			=> '/blocklist' ],
		[ '^/static_records/.+'		=> '/static_records' ],
		[ '.+'						=> '/notfound' ],
	);

	POE::Component::Server::HTTP->new(
		Address	=>		$listen_addr,
		Port	=>      $listen_port,

		TransHandler	=> [
			sub {
				my ($req, $resp) = @_;
				
				my $path = $req->uri->path; 

				# return the first matching handler
				for my $entry (@handlers) {
					my ($re, $newpath) = @$entry;

					if ($path =~ qr/$re/) {
						$req->uri->path($newpath);
						$req->header('X-Actual-Path', $path);
						last;
					}
				}

				return RC_DENY;
			},
		],
		ContentHandler	=> {
			'/notfound'							=> $callback->('notfound'),
			'/blocklist'						=> $callback->('blocklist'),
			'/static_records'					=> $callback->('static_records'),
		}, 
	);
}


#####################################################################################
# The main component of the program, which initializes and starts the others

package vpndns::server;

use Socket;
use Net::DNS::RR;
use IO::Socket::INET;
use POE qw(Component::Client::DNS Wheel::ReadWrite  Wheel::SocketFactory Filter::DNS::TCP);
use Data::Dumper;
use Net::CIDR::Set;
use Net::CIDR qw//;
use Time::HiRes;
use Net::IPAddress::Util qw/IP/;


BEGIN { vpndns::log->import; }

sub validate_config {
	my $self = shift;
# TODO
    return 1;
}

sub lookup_ns {
	my ($self, $req_domain, $req_ip) = @_;
	
	my $ns;
	#print Dumper($req_ip, $self->{ns}{range_identity});
	if (Net::CIDR::cidrlookup($req_ip, @{ $self->{ns}{range_identity} })) {
		$ns = IP($req_ip);
	}
	else {
		my $specified = $self->{ns}{specified};
		foreach my $net (keys %$specified) {
			if (Net::CIDR::cidrlookup($req_ip, $net)) {
				$ns = $specified->{$net};
			}
		}
	}

	# nameserver_regex takes precedence
	my $ns_re = $self->{config}{nameserver_regex};
	foreach my $re (keys %$ns_re) {
		if ($req_domain =~ m/$re/i) {
			$ns = IP($ns_re->{$re});
		}
	}

	if (not defined $ns) {
		return $self->{config}{nameserver_default};
	}

	return $ns->str;
}

sub build_ns_map {
	my $self = shift;

	my %specified = ();
	my @range_identity = ();
	my $id = $self->{id};

	{
		my %map = %{ $self->{config}{nameservers} };

		for my $client (keys %map) {
			# client IPs/ranges are all stored in CIDR notation (individual IPs become /32)
			if (defined (my $client_prefix = Net::CIDR::cidrvalidate($client))) {
				my $ip = IP($map{$client});

				if ($ip->is_ipv4) {
					$specified{$client_prefix} = $ip;
				} else {
					EL "nameserver $map{$client} must be an IPv4 address";
				}
			} else {
				EL "invalid IP/CIDR range $client";
			}
		}
	}

	foreach my $net (@{ $self->{config}{vpn_jail} }) {
		if (defined ($net = Net::CIDR::cidrvalidate($net))) {
			push @range_identity, $net;
		} else {
			EL "invalid IP/CIDR range $net";
		}
	}

	$self->{ns} = {

		# don't overwrite any other parts of the map
		%{ $self->{ns} },

		specified 	=> \%specified,
		range_identity	=> \@range_identity,
	};
}

# main entry point
sub start {
	my ($pkg, $config_path) = @_;

	my $self = bless {
		config 	=> {},
		ns		=> {},
	}, 'vpndns::server';

	$self->{id} = $self->{config}{id};

	$self->{sid} = POE::Session->create(
		args	=> [ $config_path ],
		object_states => [
			$self => { '_start' => 'session_start', },
			$self => [ qw(udp_read send_response dns_query listener_created listener_error ) ],
		],
		heap => {},
	)->ID();

	return $self;
}


sub session_start {
	my ($self, $k, $h, $config_path) = @_[OBJECT, KERNEL, HEAP, ARG0];

	$k->alias_set('server');

	my $config = vpndns::config::load($config_path);
	vpndns::log->start(%$config);
	L 'config loaded';

	if ($self->validate_config == 1) {
		$self->{config} = $config;
	} else {
		die; # TODO
	}

    # defaults
    my %defaults = (
        static_records_auto_ptr         => 1,
        blocklist_only_a                => 1,
        print_blocklist_read_progress   => 1,
    );

    for my $k (keys %defaults) {
        if (not defined $self->{config}{$k}) {
            $self->{config}{$k} = $defaults{$k};
        }
    }



	vpndns::interface::start(%$config);
	vpndns::blocklist::start(%$config);
	vpndns::static_records::start(%$config);

	$self->build_ns_map;
	$h->{resolver} = POE::Component::Client::DNS->spawn(Alias => "resolver");
	L 'started DNS client';

	# server ip:port -> UDP listener socket
	$h->{sockets} = {};
	# client ip:port -> server ip:port
	$h->{clients} = {};

	$h->{socketfactories} = map {
		my ($h, $p) = @$_;

		"$h:$p" => 
			POE::Wheel::SocketFactory->new(
				SocketProtocol => 'udp',
				BindAddress => $h || INADDR_ANY,
				BindPort => $p,
				SuccessEvent   => 'listener_created',
				FailureEvent   => 'listener_error',
			);
	} @{ $self->{config}{dns_listeners} };
}




sub listener_created {
  my ($k, $h, $self, $dns_socket) = @_[KERNEL,HEAP, OBJECT,ARG0];

  my ($port, $ip) = ( sockaddr_in( getsockname($dns_socket) ) );
  my $ipstr = inet_ntoa($ip);
  L sprintf("listening on %s:%d", $ipstr, $port);

  $h->{sockets}{"$ipstr:$port"} = $dns_socket;
  $k->select_read($dns_socket, 'udp_read');

  undef;
}

# Copied from POE::Component::Server::DNS
sub listener_error {
    my ($operation, $errnum, $errstr, $wheel_id) = @_[ARG0..ARG3];
    return undef if ($operation eq "read" and $errnum == 0);
    delete $_[OBJECT]->{factory};
    die "Wheel $wheel_id generated $operation error $errnum: $errstr\n";
    undef;
}

sub build_nxdomain {
	my ($query_pkt, $context) = @_;
	my ($q) = $query_pkt->question;

	my $pkt = $query_pkt->reply;
	$pkt->header->rcode('NXDOMAIN');

	return {
		host     => $q->qname,
		type     => $q->qtype,
		class    => $q->qclass,
		context  => $context,
		response => $pkt,
		error    => "",
	};
}

sub build_empty {
    my ($query_pkt, $context) = @_;
    my $pkt = $query_pkt->reply;
    my ($q) = $query_pkt->question;

    $pkt->header->ra(1);
    $pkt->header->aa(1);
    $pkt->header->rcode('NOERROR');

    return {
        host     => $q->qname,
        type     => $q->qtype,
        class    => $q->qclass,
        context  => $context,
        response => $pkt,
        error    => "",
    };
}

sub dns_query {
  	my($k, $h, $self, $session, $query_pkt) = @_[KERNEL, HEAP, OBJECT,SESSION,ARG0];

  	# $query_pkt Net::DNS::Packet
  	my ($q) = $query_pkt->question;
  	return unless $q;



		  #	next unless $q->qname =~ $handler->{match};
			#			$q->qname,
			#$q->qclass,
			#$q->qtype,
			#$query_pkt->answerfrom, $query_pkt, $handler->{'label'} );


	my $blocked_response;

	$query_pkt->answerfrom =~ m/^([0-9\.]+):(\d+)$/;

	my ($ip, $port) = ($1, $2);

	if (not $q->qclass eq 'IN') {
		EL sprintf('ignoring non-IN query from %s', $ip);
		return;
	}

	my $context = {
		af		=> $query_pkt->answerfrom,
		id		=> $query_pkt->header->id,
		ip		=> $ip,
		port	=> $port,
		ns		=> undef,
		qname	=> $q->qname,
	};

    # for requests for domains in static_records, respond to requests which aren't A or PTR with empty data
    # RFC7719 section 3
    if (defined($k->call('static_records', 'lookup_a', $q->qname)) && not $q->qtype eq 'A') {

        $k->yield('send_response', &build_empty($query_pkt, $context));
        return;
    }

    # answer A requests (or the corresponding PTR request) from static_records
    # this "for" functions as an if statement; if we use an if statement instead, the scope for $rr will
    #   not extend into the body of the if statement, unless declare $rr outside the if, which would 
    #   be undesirable
    for (my $rr; 
            ($q->qtype eq 'A' && defined($rr = $k->call('static_records', 'lookup_a', $q->qname))) 
            or ($q->qtype eq 'PTR' && defined($rr = $k->call('static_records', 'lookup_ptr', $q->qname)));
        last) {
        
        my $resp;
        my $pkt = $query_pkt->reply;
        L sprintf('resolved %s -> %s using static_records', 
            $q->qname, 
            ($q->qtype eq 'A' ? $rr->address : $rr->ptrdname));

        $pkt->push(answer => $rr);
        $pkt->header->ra(1);
        $pkt->header->aa(1);
        $pkt->header->rcode('NOERROR');

        $resp = {
            host     => $q->qname,
            type     => $q->qtype,
            class    => $q->qclass,
            context  => $context,
            response => $pkt,
            error    => "",
        };

        $k->yield('send_response', $resp);

        return;
	}



	###
	my ($blocked, $block_type) = $k->call('blocklist', 'lookup', $q->qname);
	if (defined ($blocked) && $blocked == 1) {

		if ($block_type eq '127.0.0.1') {

            if ($self->{config}{blocklist_only_a} && not $q->qtype eq 'A') {
                # Don't report the query as having been blocked
                $k->yield('send_response', &build_empty($query_pkt, $context));
                return;
            }

            my $pkt = $query_pkt->reply;
            my $rr = Net::DNS::RR->new($q->qname .'. 0 A 127.0.0.1');
            $rr->ttl(86400);
            $rr->class('IN');

            $pkt->push(answer => $rr);
            $pkt->header->ra(1);
            $pkt->header->aa(1);
            $pkt->header->rcode('NOERROR');

            $blocked_response = [
                '127.0.0.1', 
                {
                    host     => $q->qname,
                    type     => $q->qtype,
                    class    => $q->qclass,
                    context  => $context,
                    response => $pkt,
                    error    => "",
                }
            ];
		}
		elsif ($block_type eq 'NXDOMAIN') {

			$blocked_response = [
				'NXDOMAIN', 
				&build_nxdomain($query_pkt, $context),
			];
		} else {
			EL sprintf('unrecognized block type %s for %s', $block_type, $q->qname);
			return;
		}
	}
	###

#	die($k->call('blocklist', 'lookup', $q->qname));

	if (defined $blocked_response) {
		L sprintf('blocked (%s) %s %s', 
			$blocked_response->[0],
			$query_pkt->answerfrom,
			$blocked_response->[1]{host},
		);

		$k->yield('send_response', $blocked_response->[1]);
		return;
	}

	my $nameserver = $self->lookup_ns($q->qname, $ip);
	$context->{ns} = $nameserver;
	my $response;

	L $query_pkt->answerfrom. " => $nameserver : ". $q->qname;
     

    my %query = (
      class   => $q->qclass,
      type    => $q->qtype,
      host    => $q->qname,
      context => $context,
      event   => 'send_response',
	  nameservers	=> [ $nameserver ],
    );

   	$response = $h->{resolver}->resolve( %query );
    $k->yield( 'send_response', $response ) if $response;
}


sub udp_read {
	my ($k, $h, $self, $socket) = @_[KERNEL, HEAP, OBJECT, ARG0];

	my ($server_port, $server_ip) = sockaddr_in(getsockname($socket));
	my $server_ipstr = inet_ntoa($server_ip);

	my $buf = '';
	my $sockaddr;
	{ no warnings 'uninitialized';
	while (defined($sockaddr = recv($socket, $buf, 8192, 0))) {
        my ($port, $ip) = sockaddr_in($sockaddr);
        my $ipstr = inet_ntoa($ip);

		$h->{clients}{"$ipstr:$port"} = "$server_ipstr:$server_port";

        my ($packet, $error) = Net::DNS::Packet->new(\$buf);
		$packet->answerfrom("$ipstr:$port");
		$k->yield('dns_query', $packet);
	} 
	}

	return;
}

sub send_response {
	my ($k, $h, $self, $response) = @_[KERNEL, HEAP, OBJECT, ARG0];
	#print Dumper($response);

	if (not defined($response)) {
		EL 'send_response called on an undefined response';
		return;
	}
	my $context = $response->{context};

	my ($af, $id, $ip, $port, $ns, $qname) = @{ $response->{context} }{ qw/af id ip port ns qname/ };
	my $packet = $response->{response};
	if (not defined $packet) {
		EL sprintf('send_response: %s %s: error: %s', $ns, $qname, $response->{error});
		return;
	}

	$packet->header->id($id);
	$packet->answerfrom($af);
	my $sockaddr = pack_sockaddr_in($port, inet_aton($ip));

	my $socket = $h->{sockets}{ $h->{clients}{$af} };
	if (not defined $socket) {
		EL "send_response: could not find socket for $af";
		return;
	}

	my $bytes;
	{ no warnings 'uninitialized';
		$bytes = send($socket, $packet->data, undef, $sockaddr);
	}
	if (not defined $bytes) {
		EL "send_response: send: $@";
		return undef;
	}

	return $bytes;
}

__END__

